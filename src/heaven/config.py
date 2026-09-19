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

# Préfixe des variables d'environnement reconnues en mode conteneur.
ENV_PREFIX = "HEAVEN_"


@dataclass(slots=True)
class RetentionPolicy:
    """Nombre d'instantanés à conserver par période. 0 signifie « aucun de cette période »."""

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
    follow_symlinks: bool = False
    compress: bool = True
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)

    def __post_init__(self) -> None:
        if not self.sources:
            raise ConfigError("au moins une source est requise")


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
        retention=retention,
    )


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
