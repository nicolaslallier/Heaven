"""Chargement de la configuration (fichier TOML ou variables d'environnement)."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_CONFIG_NAMES = ("heaven.toml", ".heaven.toml")

CHUNKING_MODES = ("none", "cdc")

# Préfixe des variables d'environnement reconnues en mode conteneur.
ENV_PREFIX = "HEAVEN_"

DEFAULT_JOB = "default"
_JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_BACKUP_KEYS = {"sources", "excludes", "follow_symlinks", "compress", "chunking"}


@dataclass(slots=True)
class RetentionPolicy:
    """Nombre d'instantanés à conserver par période. 0 signifie « aucun de cette période ».

    Attention, les défauts diffèrent selon la voie de configuration : dans un
    fichier TOML, une clé omise de `[retention]` vaut `keep_last = 7` (défaut de
    ce champ) ; par l'environnement (`config_from_env`), une variable absente
    vaut 0, donc sans aucune `HEAVEN_KEEP_*` tout est conservé.
    """

    keep_last: int = 7
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0

    def is_empty(self) -> bool:
        return not any((self.keep_last, self.keep_daily, self.keep_weekly, self.keep_monthly))


@dataclass(slots=True)
class Config:
    """Configuration d'une tâche de sauvegarde."""

    sources: list[Path]
    repository: Path
    excludes: list[str] = field(default_factory=list)
    # True : on descend dans les liens vers des dossiers (boucles détectées et ignorées).
    # Les liens vers des fichiers restent stockés comme liens, jamais comme leur contenu.
    follow_symlinks: bool = False
    compress: bool = True
    chunking: str = "none"  # "cdc" : les gros fichiers sont découpés en blocs dédupliqués
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)

    def __post_init__(self) -> None:
        if self.chunking not in CHUNKING_MODES:
            raise ConfigError(
                f"chunking doit valoir {' ou '.join(CHUNKING_MODES)} (reçu : {self.chunking!r})"
            )
        if not self.sources:
            raise ConfigError("au moins une source est requise")


@dataclass(slots=True)
class Job:
    """Une sauvegarde planifiée indépendante : ses sources, son horaire, sa rétention.

    Les jobs d'un même service partagent le dépôt (et donc la déduplication) ; leurs
    instantanés sont distingués par l'étiquette `name`.
    """

    name: str
    config: Config
    schedule: str | None = None  # None : planification par défaut du service
    verify_every: int | None = None  # None : réglage par défaut du service


def find_config(start: Path | None = None) -> Path | None:
    """Cherche un fichier de configuration dans `start` puis ses répertoires parents."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_config(path: Path) -> Config:
    """Lit et valide un fichier de configuration TOML."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration introuvable : {path}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"configuration invalide ({path}) : {exc}") from exc
    return config_from_dict(raw, base_dir=path.parent)


def load_jobs(path: Path) -> list[Job] | None:
    """Lit les `[[jobs]]` d'un fichier TOML, ou `None` si le fichier n'en déclare pas."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration introuvable : {path}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"configuration invalide ({path}) : {exc}") from exc
    return jobs_from_dict(raw, base_dir=path.parent)


def jobs_from_dict(raw: dict[str, Any], base_dir: Path) -> list[Job] | None:
    jobs_raw = raw.get("jobs")
    if jobs_raw is None:
        return None
    if not (isinstance(jobs_raw, list) and jobs_raw and all(isinstance(j, dict) for j in jobs_raw)):
        raise ConfigError("jobs doit être une liste de tables ([[jobs]])")

    base = {key: value for key, value in raw.items() if key != "jobs"}
    shared = base.get("backup", base)
    if not isinstance(shared, dict):
        raise ConfigError("la section [backup] doit être une table")

    jobs: list[Job] = []
    for item in jobs_raw:
        name = validate_job_name(item.get("name"))
        if any(job.name == name for job in jobs):
            raise ConfigError(f"job en double : {name!r}")
        unknown = set(item) - {
            "name", "schedule", "verify_every", "sources", "excludes",
            "follow_symlinks", "compress", "chunking", "retention",
        }  # fmt: skip
        if unknown:
            raise ConfigError(f"job {name!r} : clés inconnues : {', '.join(sorted(unknown))}")
        merged_backup = {**shared, **{k: v for k, v in item.items() if k in _BACKUP_KEYS}}
        retention = item.get("retention", base.get("retention", {}))
        merged = {"backup": merged_backup, "retention": retention}
        schedule = item.get("schedule")
        verify_every = item.get("verify_every")
        if schedule is not None and not isinstance(schedule, str):
            raise ConfigError(f"job {name!r} : schedule doit être du texte")
        if verify_every is not None and (
            not isinstance(verify_every, int) or isinstance(verify_every, bool) or verify_every < 0
        ):
            raise ConfigError(f"job {name!r} : verify_every doit être un entier positif ou nul")
        jobs.append(
            Job(
                name=name,
                config=config_from_dict(merged, base_dir),
                schedule=schedule,
                verify_every=verify_every,
            )
        )
    return jobs


def validate_job_name(value: object) -> str:
    if not isinstance(value, str) or not _JOB_NAME.match(value):
        raise ConfigError(
            f"nom de job invalide : {value!r} (lettres, chiffres, « - » et « _ » seulement)"
        )
    return value


def config_from_dict(raw: dict[str, Any], base_dir: Path) -> Config:
    """Construit une `Config` depuis un dictionnaire, en résolvant les chemins relatifs."""
    backup = raw.get("backup", raw)
    if not isinstance(backup, dict):
        raise ConfigError("la section [backup] doit être une table")

    sources = backup.get("sources")
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
        raise ConfigError("backup.sources doit être une liste de chemins")
    if not sources:
        raise ConfigError("backup.sources ne doit pas être vide")

    repository = backup.get("repository")
    if not isinstance(repository, str):
        raise ConfigError("backup.repository doit être un chemin")

    excludes = backup.get("excludes", [])
    if not isinstance(excludes, list) or not all(isinstance(item, str) for item in excludes):
        raise ConfigError("backup.excludes doit être une liste de motifs")

    retention_raw = raw.get("retention", {})
    if not isinstance(retention_raw, dict):
        raise ConfigError("la section [retention] doit être une table")
    unknown = set(retention_raw) - {"keep_last", "keep_daily", "keep_weekly", "keep_monthly"}
    if unknown:
        raise ConfigError(f"clés de rétention inconnues : {', '.join(sorted(unknown))}")
    for key, value in retention_raw.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"retention.{key} doit être un entier positif ou nul")

    return Config(
        sources=[_resolve(item, base_dir) for item in sources],
        repository=_resolve(repository, base_dir),
        excludes=excludes,
        follow_symlinks=bool(backup.get("follow_symlinks", False)),
        compress=bool(backup.get("compress", True)),
        chunking=backup.get("chunking", "none"),
        retention=RetentionPolicy(**retention_raw),
    )


def _resolve(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


# -- configuration par variables d'environnement (mode conteneur) --------


def config_from_env(env: Mapping[str, str] | None = None) -> Config | None:
    """Construit une configuration depuis l'environnement, ou `None` si rien n'est défini.

    C'est la voie utilisée dans un conteneur : le fichier `heaven.toml` devient
    facultatif et tout se règle avec les variables de la pile (Portainer, compose).

    Rétention : une variable `HEAVEN_KEEP_*` absente vaut 0 ici (tout conserver
    si aucune n'est définie), contrairement au TOML où `keep_last` vaut 7 quand
    la clé est omise de `[retention]`. L'image Docker définit ses propres valeurs.
    """
    values = os.environ if env is None else env
    sources = _split_list(values.get(f"{ENV_PREFIX}SOURCES"))
    repository = (values.get(f"{ENV_PREFIX}REPOSITORY") or "").strip()
    if not sources and not repository:
        return None
    if not sources:
        raise ConfigError(f"{ENV_PREFIX}SOURCES est requis avec {ENV_PREFIX}REPOSITORY")
    if not repository:
        raise ConfigError(f"{ENV_PREFIX}REPOSITORY est requis avec {ENV_PREFIX}SOURCES")

    retention = RetentionPolicy(
        keep_last=_int(values, f"{ENV_PREFIX}KEEP_LAST", 0),
        keep_daily=_int(values, f"{ENV_PREFIX}KEEP_DAILY", 0),
        keep_weekly=_int(values, f"{ENV_PREFIX}KEEP_WEEKLY", 0),
        keep_monthly=_int(values, f"{ENV_PREFIX}KEEP_MONTHLY", 0),
    )
    return Config(
        sources=[Path(item).expanduser() for item in sources],
        repository=Path(repository).expanduser(),
        excludes=_split_list(values.get(f"{ENV_PREFIX}EXCLUDES")),
        follow_symlinks=_bool(values, f"{ENV_PREFIX}FOLLOW_SYMLINKS", False),
        compress=_bool(values, f"{ENV_PREFIX}COMPRESS", True),
        chunking=(values.get(f"{ENV_PREFIX}CHUNKING") or "none").strip().lower(),
        retention=retention,
    )


def jobs_from_env(env: Mapping[str, str] | None = None) -> list[Job] | None:
    """Jobs déclarés par `HEAVEN_JOBS=docs,photos`, ou `None` si la variable est absente.

    Chaque job lit `HEAVEN_JOB_<NOM>_SCHEDULE`, `_SOURCES`, `_EXCLUDES`, `_KEEP_*`
    et `_VERIFY_EVERY` ; ce qu'il ne définit pas vient des variables communes
    (`HEAVEN_SOURCES`, `HEAVEN_KEEP_*`…).
    """
    values = os.environ if env is None else env
    names = _split_list(values.get(f"{ENV_PREFIX}JOBS"))
    if not names:
        return None
    repository = (values.get(f"{ENV_PREFIX}REPOSITORY") or "").strip()
    if not repository:
        raise ConfigError(f"{ENV_PREFIX}REPOSITORY est requis avec {ENV_PREFIX}JOBS")

    jobs: list[Job] = []
    for name in names:
        validate_job_name(name)
        if any(job.name == name for job in jobs):
            raise ConfigError(f"job en double dans {ENV_PREFIX}JOBS : {name!r}")
        prefix = f"{ENV_PREFIX}JOB_{re.sub(r'[^A-Z0-9]', '_', name.upper())}_"
        sources = _split_list(values.get(f"{prefix}SOURCES")) or _split_list(
            values.get(f"{ENV_PREFIX}SOURCES")
        )
        if not sources:
            raise ConfigError(f"job {name!r} : {prefix}SOURCES (ou {ENV_PREFIX}SOURCES) requis")
        keys = ("KEEP_LAST", "KEEP_DAILY", "KEEP_WEEKLY", "KEEP_MONTHLY")
        if any((values.get(prefix + key) or "").strip() for key in keys):
            retention = RetentionPolicy(*(_int(values, prefix + key, 0) for key in keys))
        else:
            retention = RetentionPolicy(*(_int(values, ENV_PREFIX + key, 0) for key in keys))
        excludes_raw = values.get(f"{prefix}EXCLUDES")
        verify = (values.get(f"{prefix}VERIFY_EVERY") or "").strip()
        jobs.append(
            Job(
                name=name,
                config=Config(
                    sources=[Path(item).expanduser() for item in sources],
                    repository=Path(repository).expanduser(),
                    excludes=_split_list(excludes_raw)
                    if excludes_raw is not None
                    else _split_list(values.get(f"{ENV_PREFIX}EXCLUDES")),
                    follow_symlinks=_bool(values, f"{ENV_PREFIX}FOLLOW_SYMLINKS", False),
                    compress=_bool(values, f"{ENV_PREFIX}COMPRESS", True),
                    chunking=(values.get(f"{ENV_PREFIX}CHUNKING") or "none").strip().lower(),
                    retention=retention,
                ),
                schedule=(values.get(f"{prefix}SCHEDULE") or "").strip() or None,
                verify_every=_int(values, prefix + "VERIFY_EVERY", 0) if verify else None,
            )
        )
    return jobs


def _split_list(value: str | None) -> list[str]:
    """Découpe une liste écrite « a:b » ou « a, b » ; les éléments vides sont ignorés."""
    if not value:
        return []
    return [item.strip() for item in re.split(r"[:,\n]", value) if item.strip()]


def _bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on", "oui"}:
        return True
    if normalized in {"0", "false", "no", "off", "non"}:
        return False
    raise ConfigError(f"{key} doit valoir « true » ou « false » (reçu : {raw!r})")


def _int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} doit être un entier (reçu : {raw!r})") from exc
    if parsed < 0:
        raise ConfigError(f"{key} doit être un entier positif ou nul (reçu : {raw!r})")
    return parsed
