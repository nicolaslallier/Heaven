"""Mode appliance : sauvegardes planifiées, en boucle, dans un conteneur.

`heaven serve` enchaîne indéfiniment : sauvegarde → rétention → vérification
périodique. Chaque cycle écrit un fichier d'état que `heaven health` relit :
c'est lui qui alimente la sonde de santé Docker affichée par Portainer.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import engine
from .config import ENV_PREFIX, Config
from .errors import ConfigError, HeavenError
from .repository import Repository

if TYPE_CHECKING:
    from .notify import Notifier

DEFAULT_STATE_PATH = Path("/var/lib/heaven/state.json")
DEFAULT_SCHEDULE = "02:30"
DEFAULT_JOB = "default"
STATE_FORMAT = 2
HISTORY_LIMIT = 200

Logger = Callable[[str], None]

_INTERVAL = re.compile(r"^(\d+)\s*(s|m|h|d)$", re.IGNORECASE)
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


# -- planification -------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Schedule:
    """Quand déclencher une sauvegarde : soit un intervalle, soit des heures fixes."""

    spec: str
    interval: timedelta | None = None
    times: tuple[time, ...] = ()

    def next_run(self, after: datetime) -> datetime:
        """Prochain déclenchement strictement après `after`."""
        if self.interval is not None:
            return after + self.interval
        return min(_next_daily(after, moment) for moment in self.times)

    def __str__(self) -> str:
        return self.spec


def parse_schedule(spec: str) -> Schedule:
    """Lit « 6h », « 90m », « 02:30 » ou « 02:30,14:00 » (aussi « daily@02:30 »)."""
    cleaned = (spec or "").strip()
    if not cleaned:
        raise ConfigError("la planification ne doit pas être vide")
    for prefix in ("daily@", "quotidien@", "every@", "@"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break

    interval = _INTERVAL.match(cleaned)
    if interval:
        amount = int(interval.group(1))
        if amount <= 0:
            raise ConfigError(f"intervalle de planification nul ou négatif : {spec!r}")
        delta = timedelta(**{_UNITS[interval.group(2).lower()]: amount})
        return Schedule(spec=cleaned, interval=delta)

    times: list[time] = []
    for item in (part.strip() for part in cleaned.split(",")):
        if not item:
            continue
        match = _TIME.match(item)
        if not match:
            raise ConfigError(
                f"planification invalide : {spec!r} "
                "(attendu « 6h », « 90m », « 02:30 » ou « 02:30,14:00 »)"
            )
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ConfigError(f"heure de planification invalide : {item!r}")
        times.append(time(hour=hour, minute=minute))
    if not times:
        raise ConfigError(f"planification invalide : {spec!r}")
    return Schedule(spec=cleaned, times=tuple(sorted(set(times))))


def _next_daily(after: datetime, moment: time) -> datetime:
    candidate = after.replace(hour=moment.hour, minute=moment.minute, second=0, microsecond=0)
    return candidate if candidate > after else candidate + timedelta(days=1)


# -- exécution d'un cycle ------------------------------------------------


@dataclass(slots=True)
class CycleReport:
    """Résumé d'un cycle, tel qu'il est écrit dans le fichier d'état."""

    started_at: str
    job: str | None = None
    finished_at: str | None = None
    status: str = "running"  # running | ok | error
    snapshot: str | None = None
    files: int = 0
    bytes_stored: int = 0
    snapshots_removed: int = 0
    bytes_freed: int = 0
    verified: bool = False
    verify: dict[str, int] | None = None
    repository_bytes: int | None = None
    stats: dict[str, int] = field(default_factory=dict)  # hors fichier d'état
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "job": self.job,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "snapshot": self.snapshot,
            "files": self.files,
            "bytes_stored": self.bytes_stored,
            "snapshots_removed": self.snapshots_removed,
            "bytes_freed": self.bytes_freed,
            "verified": self.verified,
            "verify": self.verify,
            "repository_bytes": self.repository_bytes,
            "error": self.error,
            "warnings": self.warnings,
        }


def run_cycle(
    config: Config,
    tag: str | None = None,
    verify: bool = False,
    log: Logger | None = None,
    now: Callable[[], datetime] = datetime.now,
    job: str | None = None,
) -> CycleReport:
    """Sauvegarde, applique la rétention, puis vérifie le dépôt si demandé.

    Avec `job`, la rétention ne touche que les instantanés de ce job (son étiquette).

    Les erreurs attendues sont capturées : une appliance ne doit pas s'arrêter
    parce qu'une sauvegarde a échoué, elle réessaie au cycle suivant.
    """
    emit = log or _discard
    report = CycleReport(started_at=now().isoformat(timespec="seconds"), job=job)
    try:
        _check_sources(config, report, emit)
        emit(f"sauvegarde de {', '.join(str(source) for source in config.sources)}")
        result = engine.backup(config, tag=tag)
        report.snapshot = result.snapshot.id
        report.files = result.files_total
        report.bytes_stored = result.bytes_stored
        emit(
            f"instantané {result.snapshot.id} : {result.files_total} fichiers, "
            f"{result.files_stored} nouveaux ({result.bytes_stored} octets écrits), "
            f"{result.files_reused} réutilisés"
        )
        for path, reason in result.skipped:
            message = f"ignoré {path} : {reason}"
            report.warnings.append(message)
            emit(f"attention : {message}")

        if not config.retention.is_empty() and report.files == 0:
            # Filet de sécurité : une sauvegarde vide ne doit jamais faire tourner la
            # rétention, sinon un volume démonté finirait par effacer les bons instantanés.
            emit("attention : instantané vide, rétention non appliquée")
        elif not config.retention.is_empty():
            pruned = engine.prune(config.repository, config.retention, tag=job)
            report.snapshots_removed = len(pruned.removed)
            report.bytes_freed = pruned.bytes_freed
            emit(
                f"rétention : {len(pruned.kept)} instantané(s) conservé(s), "
                f"{len(pruned.removed)} supprimé(s), {pruned.bytes_freed} octets libérés"
            )

        if verify:
            checked = engine.verify(config.repository)
            report.verified = True
            report.verify = {
                "objects_checked": checked.objects_checked,
                "missing": len(checked.missing),
                "corrupted": len(checked.corrupted),
            }
            if not checked.ok:
                report.status = "error"
                report.error = (
                    f"dépôt corrompu : {len(checked.missing)} objet(s) manquant(s), "
                    f"{len(checked.corrupted)} corrompu(s)"
                )
                emit(f"ERREUR {report.error}")
            else:
                emit(f"vérification : {checked.objects_checked} objet(s) intègre(s)")

        if report.status == "running":
            report.status = "ok"
    except (HeavenError, OSError) as error:
        report.status = "error"
        report.error = str(error)
        emit(f"ERREUR cycle interrompu : {error}")

    report.stats = repository_stats(config.repository)
    report.repository_bytes = report.stats.get("bytes")
    report.finished_at = now().isoformat(timespec="seconds")
    return report


def repository_stats(path: Path) -> dict[str, int]:
    """Taille, objets et instantanés. Parcourt le dépôt : jamais à chaque requête."""
    total = objects = 0
    try:
        repository = Repository.open(path)
        for prefix in repository.objects_dir.iterdir():
            if not prefix.is_dir():
                continue
            for item in prefix.iterdir():
                with contextlib.suppress(OSError):
                    total += item.stat().st_size
                    objects += 1
        return {
            "bytes": total,
            "objects": objects,
            "snapshots": len(repository.list_snapshot_ids()),
        }
    except (HeavenError, OSError):
        return {}


def _check_sources(config: Config, report: CycleReport, emit: Logger) -> None:
    """Refuse de sauvegarder si plus aucune source n'est là : volume oublié ou démonté."""
    missing = [source for source in config.sources if not source.exists()]
    if len(missing) == len(config.sources):
        raise HeavenError(
            "aucune source accessible : "
            + ", ".join(str(source) for source in missing)
            + " (volume non monté ?)"
        )
    for source in missing:
        message = f"source absente, ignorée : {source}"
        report.warnings.append(message)
        emit(f"attention : {message}")


# -- boucle de service ---------------------------------------------------


@dataclass(slots=True)
class ScheduledJob:
    """Un job en cours de service : sa planification et où il en est."""

    name: str
    config: Config
    schedule: Schedule
    tag: str | None = None
    verify_every: int = 0
    cycles: int = 0
    pending: datetime = field(default_factory=datetime.now)
    last: CycleReport | None = None


def serve(
    config: Config,
    schedule: Schedule,
    tag: str | None = None,
    initial_backup: bool = True,
    verify_every: int = 0,
    state_path: Path | None = None,
    stop: threading.Event | None = None,
    log: Logger | None = None,
    max_cycles: int | None = None,
    now: Callable[[], datetime] = datetime.now,
    notifier: Notifier | None = None,
) -> int:
    """Service à un seul job : voir `serve_jobs`."""
    job = ScheduledJob(
        name=tag or DEFAULT_JOB,
        config=config,
        schedule=schedule,
        tag=tag,
        verify_every=verify_every,
    )
    return serve_jobs(
        [job], initial_backup, state_path, stop, log, max_cycles, now, notifier, scoped=False
    )


def serve_jobs(
    jobs: list[ScheduledJob],
    initial_backup: bool = True,
    state_path: Path | None = None,
    stop: threading.Event | None = None,
    log: Logger | None = None,
    max_cycles: int | None = None,
    now: Callable[[], datetime] = datetime.now,
    notifier: Notifier | None = None,
    scoped: bool = True,
) -> int:
    """Boucle de sauvegarde jusqu'à l'arrêt du conteneur (SIGTERM) ou `max_cycles`.

    Chaque job a sa propre planification ; ils s'exécutent l'un après l'autre (un seul
    écrivain à la fois dans le dépôt), le plus en retard d'abord. `scoped` limite la
    rétention de chaque job à ses propres instantanés.

    Retourne 0 si le dernier cycle de chaque job a réussi, 1 sinon.
    """
    if not jobs:
        raise ConfigError("aucun job à planifier")
    emit = log or log_line
    stopping = stop if stop is not None else _install_signal_handlers(emit)
    state = state_path if state_path is not None else default_state_path()
    repository = jobs[0].config.repository
    if any(job.config.repository != repository for job in jobs):
        raise ConfigError("tous les jobs doivent écrire dans le même dépôt")

    for job in jobs:
        emit(f"démarrage : « {job.name} », planification « {job.schedule} », dépôt {repository}")
    _prepare_repository(jobs[0].config, emit)
    cycles = 0
    memory = load_memory(state)
    if len(jobs) == 1 and memory.last_run and jobs[0].name not in memory.jobs:
        # État d'avant les jobs : son dernier cycle appartient au job unique.
        memory.jobs[jobs[0].name] = {
            "last_run": memory.last_run,
            "last_verify": memory.last_verify,
        }
    for job in jobs:
        job.pending = now() if initial_backup else job.schedule.next_run(now())
        if not initial_backup:
            emit(f"[{job.name}] première sauvegarde prévue à {_iso(job.pending)}")
    _write_state(state, jobs, memory, emit, notifier, now())

    while not stopping.is_set():
        job = min(jobs, key=lambda candidate: candidate.pending)
        delay = (job.pending - now()).total_seconds()
        if delay > 0:
            # `wait` rend la main immédiatement sur SIGTERM : pas d'arrêt brutal.
            if stopping.wait(timeout=min(delay, 60.0)):
                break
            # Rappels et retards se détectent entre deux cycles, pas seulement après.
            if notifier is not None:
                _write_state(state, jobs, memory, emit, notifier, now())
            continue

        cycles += 1
        job.cycles += 1
        verify = bool(job.verify_every) and job.cycles % job.verify_every == 0
        prefix = f"[{job.name}] " if len(jobs) > 1 else ""

        def say(message: str, prefix: str = prefix) -> None:
            emit(prefix + message)

        job.last = run_cycle(
            job.config,
            tag=job.tag,
            verify=verify,
            log=say,
            now=now,
            job=job.name if scoped else None,
        )
        job.pending = job.schedule.next_run(now())
        memory.record(job.last, job.last.stats, job.name)
        _write_state(state, jobs, memory, emit, notifier, now())
        emit(f"{prefix}prochaine sauvegarde à {_iso(job.pending)}")
        if max_cycles is not None and cycles >= max_cycles:
            break

    emit("arrêt du service")
    return 0 if all(job.last is None or job.last.status == "ok" for job in jobs) else 1


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _prepare_repository(config: Config, emit: Logger) -> None:
    """Crée le dépôt au démarrage plutôt qu'à la première échéance.

    Deux raisons : un volume mal monté se voit tout de suite dans les journaux au
    lieu d'attendre 02:30, et le dépôt existe avant l'écriture du fichier d'état —
    sans quoi un état rangé dans le dépôt le rendrait « non vide », ce que
    `Repository.initialize` refuse à raison.
    """
    try:
        Repository.initialize(config.repository)
        emit(f"dépôt prêt : {config.repository}")
    except (HeavenError, OSError) as error:
        # Pas fatal : le montage peut arriver en retard, chaque cycle réessaiera.
        emit(f"ERREUR dépôt inutilisable : {error}")


def _install_signal_handlers(log: Logger) -> threading.Event:
    stopping = threading.Event()

    def handle(signum: int, _frame: object) -> None:
        log(f"signal {signal.Signals(signum).name} reçu : arrêt après le cycle en cours")
        stopping.set()

    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, handle)
    return stopping


# -- état et santé -------------------------------------------------------


def default_state_path() -> Path:
    """Fichier d'état : `HEAVEN_STATE` sinon `/var/lib/heaven/state.json`."""
    configured = os.environ.get(f"{ENV_PREFIX}STATE")
    return Path(configured).expanduser() if configured else DEFAULT_STATE_PATH


@dataclass(slots=True)
class Memory:
    """Ce qui survit aux redémarrages : historique, dernière vérification, notifications."""

    history: list[dict[str, object]] = field(default_factory=list)
    last_run: dict[str, object] | None = None
    last_verify: dict[str, object] | None = None
    repository: dict[str, int] | None = None
    notify: dict[str, object] = field(default_factory=dict)
    # Par job : dernier cycle et dernière vérification (ce qui décide de son état).
    jobs: dict[str, dict[str, object]] = field(default_factory=dict)

    def record(
        self, report: CycleReport, repository: dict[str, int] | None = None, job: str | None = None
    ) -> None:
        if repository:
            self.repository = repository
        entry = report.to_dict()
        self.last_run = entry
        self.history.insert(0, entry)
        del self.history[HISTORY_LIMIT:]
        mine = self.jobs.setdefault(job or DEFAULT_JOB, {})
        mine["last_run"] = entry
        if report.verify is not None:
            verify: dict[str, object] = {
                "at": report.finished_at,
                "ok": not (report.verify["missing"] or report.verify["corrupted"]),
                **report.verify,
            }
            self.last_verify = mine["last_verify"] = verify


def load_memory(path: Path) -> Memory:
    """Relit l'état précédent ; un `format: 1` (sans historique) est accepté tel quel."""
    try:
        raw = read_state(path)
    except HeavenError:
        return Memory()
    history = raw.get("history")
    notify = raw.get("notify")
    last_run, last_verify, repository = (
        raw.get("last_run"),
        raw.get("last_verify"),
        raw.get("repository"),
    )
    jobs: dict[str, dict[str, object]] = {}
    saved_jobs = raw.get("jobs")
    for item in saved_jobs if isinstance(saved_jobs, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            jobs[item["name"]] = {
                "last_run": item.get("last_run"),
                "last_verify": item.get("last_verify"),
            }
    return Memory(
        jobs=jobs,
        history=[run for run in history if isinstance(run, dict)][:HISTORY_LIMIT]
        if isinstance(history, list)
        else [],
        last_run=last_run if isinstance(last_run, dict) else None,
        last_verify=last_verify if isinstance(last_verify, dict) else None,
        repository=repository if isinstance(repository, dict) else None,
        notify=notify if isinstance(notify, dict) else {},
    )


def _write_state(
    path: Path,
    jobs: list[ScheduledJob],
    memory: Memory,
    log: Logger,
    notifier: Notifier | None = None,
    moment: datetime | None = None,
) -> None:
    """Écrit l'état. Le haut du document résume tous les jobs (le plus mal en point
    d'abord) pour que la sonde de santé, l'API et les notifications n'aient rien à
    savoir des jobs ; `jobs` détaille chacun.
    """
    details: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []  # derniers cycles, tous jobs confondus
    verifies: list[dict[str, object]] = []
    for job in jobs:
        saved = memory.jobs.get(job.name, {})
        last_run, last_verify = saved.get("last_run"), saved.get("last_verify")
        if isinstance(last_run, dict):
            runs.append(last_run)
        if isinstance(last_verify, dict):
            verifies.append(last_verify)
        details.append(
            {
                "name": job.name,
                "schedule": str(job.schedule),
                "status": str(last_run["status"]) if isinstance(last_run, dict) else "pending",
                "next_run": _iso(job.pending),
                "last_run": last_run if isinstance(last_run, dict) else None,
                "last_verify": last_verify if isinstance(last_verify, dict) else None,
            }
        )

    # Le résumé montre le pire : un job en échec, sinon le cycle le plus récent.
    failed = [run for run in runs if run.get("status") == "error"]
    latest = sorted(runs, key=lambda run: str(run.get("finished_at") or ""), reverse=True)
    shown = (failed or latest)[0] if (failed or latest) else None
    bad_verify = [verify for verify in verifies if verify.get("ok") is False]
    recent_verify = sorted(verifies, key=lambda verify: str(verify.get("at") or ""), reverse=True)
    shown_verify = (bad_verify or recent_verify)[0] if (bad_verify or recent_verify) else None
    payload: dict[str, object] = {
        "format": STATE_FORMAT,
        "schedule": str(jobs[0].schedule)
        if len(jobs) == 1
        else "; ".join(f"{job.name}: {job.schedule}" for job in jobs),
        "status": str(shown["status"]) if shown else "pending",
        "next_run": min(_iso(job.pending) for job in jobs),
        "last_run": shown,
        "last_verify": shown_verify,
        "repository": memory.repository,
        "history": memory.history,
        "notify": memory.notify,
        "jobs": details,
    }
    if notifier is not None:
        notifier.check(payload, memory.notify, moment or datetime.now())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        # L'état n'est qu'un indicateur : son écriture ne doit jamais tuer le service.
        log(f"attention : état non écrit ({path}) : {error}")


def read_state(path: Path) -> dict[str, object]:
    """Relit le fichier d'état écrit par `serve`."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HeavenError(f"aucun état : {path} (le service n'a jamais démarré ?)") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HeavenError(f"état illisible ({path}) : {exc}") from exc
    if not isinstance(raw, dict):
        raise HeavenError(f"état illisible ({path}) : objet JSON attendu")
    return raw


def health(state_path: Path, grace: timedelta, now: datetime | None = None) -> tuple[bool, str]:
    """Dit si le service est en bonne santé, et pourquoi. Sonde de santé du conteneur."""
    state = read_state(state_path)
    moment = now or datetime.now()
    status = state.get("status")
    recorded = state.get("last_run")
    last: dict[str, object] = recorded if isinstance(recorded, dict) else {}

    if status == "error":
        where = f" (job {last['job']})" if last.get("job") else ""
        return False, f"dernier cycle en échec{where} : {last.get('error') or 'raison inconnue'}"

    next_run = state.get("next_run")
    if isinstance(next_run, str):
        try:
            deadline = datetime.fromisoformat(next_run) + grace
        except ValueError:
            return False, f"date de prochaine sauvegarde illisible : {next_run!r}"
        if moment > deadline and status != "running":
            return False, f"sauvegarde en retard : prévue à {next_run}"

    snapshot = last.get("snapshot")
    if status == "pending":
        return True, f"en attente de la première sauvegarde (prévue à {next_run})"
    return True, f"dernier instantané {snapshot or '-'}, prochaine sauvegarde à {next_run}"


def log_line(message: str) -> None:
    """Journal horodaté sur la sortie standard : c'est ce que Portainer affiche."""
    print(f"{datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def _discard(_message: str) -> None:
    pass
