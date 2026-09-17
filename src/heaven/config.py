"""Chargement de la configuration (format TOML)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_CONFIG_NAMES = ("heaven.toml", ".heaven.toml")


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
