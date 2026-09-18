"""Mode appliance : sauvegardes planifiées, en boucle, dans un conteneur.

`heaven serve` enchaîne indéfiniment : sauvegarde → rétention → vérification
périodique. Chaque cycle écrit un fichier d'état que `heaven health` relit :
c'est lui qui alimente la sonde de santé Docker affichée par Portainer.
"""

from __future__ import annotations

import json
import os
import re
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from . import engine
from .config import ENV_PREFIX, Config
from .errors import ConfigError, HeavenError

DEFAULT_STATE_PATH = Path("/var/lib/heaven/state.json")
DEFAULT_SCHEDULE = "02:30"
STATE_FORMAT = 1

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
    finished_at: str | None = None
    status: str = "running"  # running | ok | error
    snapshot: str | None = None
    files: int = 0
    bytes_stored: int = 0
    snapshots_removed: int = 0
    bytes_freed: int = 0
    verified: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "snapshot": self.snapshot,
            "files": self.files,
            "bytes_stored": self.bytes_stored,
            "snapshots_removed": self.snapshots_removed,
            "bytes_freed": self.bytes_freed,
            "verified": self.verified,
            "error": self.error,
            "warnings": self.warnings,
        }


def run_cycle(
    config: Config,
    tag: str | None = None,
    verify: bool = False,
    log: Logger | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> CycleReport:
    """Sauvegarde, applique la rétention, puis vérifie le dépôt si demandé.

    Les erreurs attendues sont capturées : une appliance ne doit pas s'arrêter
    parce qu'une sauvegarde a échoué, elle réessaie au cycle suivant.
    """
    emit = log or _discard
    report = CycleReport(started_at=now().isoformat(timespec="seconds"))
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
            pruned = engine.prune(config.repository, config.retention)
            report.snapshots_removed = len(pruned.removed)
            report.bytes_freed = pruned.bytes_freed
            emit(
                f"rétention : {len(pruned.kept)} instantané(s) conservé(s), "
                f"{len(pruned.removed)} supprimé(s), {pruned.bytes_freed} octets libérés"
            )

        if verify:
            checked = engine.verify(config.repository)
            report.verified = True
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

    report.finished_at = now().isoformat(timespec="seconds")
    return report


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
) -> int:
    """Boucle de sauvegarde jusqu'à l'arrêt du conteneur (SIGTERM) ou `max_cycles`.

    Retourne 0 si tous les cycles exécutés ont réussi, 1 si le dernier a échoué.
    """
    emit = log or _log
    stopping = stop if stop is not None else _install_signal_handlers(emit)
    state = state_path if state_path is not None else default_state_path()

    emit(f"démarrage : planification « {schedule} », dépôt {config.repository}")
    cycles = 0
    last: CycleReport | None = None
    pending = now() if initial_backup else schedule.next_run(now())
    if not initial_backup:
        emit(f"première sauvegarde prévue à {pending.isoformat(timespec='seconds')}")
    _write_state(state, schedule, last, pending, emit)

    while not stopping.is_set():
        delay = (pending - now()).total_seconds()
        if delay > 0:
            # `wait` rend la main immédiatement sur SIGTERM : pas d'arrêt brutal.
            if stopping.wait(timeout=min(delay, 60.0)):
                break
            continue

        cycles += 1
        verify = bool(verify_every) and cycles % verify_every == 0
        last = run_cycle(config, tag=tag, verify=verify, log=emit, now=now)
        pending = schedule.next_run(now())
        _write_state(state, schedule, last, pending, emit)
        emit(f"prochaine sauvegarde à {pending.isoformat(timespec='seconds')}")
        if max_cycles is not None and cycles >= max_cycles:
            break

    emit("arrêt du service")
    return 0 if last is None or last.status == "ok" else 1


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


def _write_state(
    path: Path,
    schedule: Schedule,
    last: CycleReport | None,
    next_run: datetime,
    log: Logger,
) -> None:
    payload = {
        "format": STATE_FORMAT,
        "schedule": str(schedule),
        "status": last.status if last else "pending",
        "next_run": next_run.isoformat(timespec="seconds"),
        "last_run": last.to_dict() if last else None,
    }
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
        return False, f"dernier cycle en échec : {last.get('error') or 'raison inconnue'}"

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


def _log(message: str) -> None:
    """Journal horodaté sur la sortie standard : c'est ce que Portainer affiche."""
    print(f"{datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def _discard(_message: str) -> None:
    pass
