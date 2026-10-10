"""Indicateur global du tableau de bord : une seule règle, côté serveur.

La même évaluation alimente l'API (`status` dans /api/health) et les
notifications : ainsi l'écran et le courriel ne peuvent pas se contredire.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import ENV_PREFIX

DEFAULT_DISK_WARN = 20.0
DEFAULT_DISK_CRIT = 10.0
GROWTH_WINDOW = timedelta(days=30)


@dataclass(slots=True, frozen=True)
class Verdict:
    status: str  # ok | warning | error | unknown
    kind: str  # ok | pending | warnings | disk_low | failed | corrupted | late | disk_critical
    reason: str


def disk_thresholds() -> tuple[float, float]:
    """Seuils d'espace libre (en %) : `HEAVEN_DISK_WARN` puis `HEAVEN_DISK_CRIT`."""
    return (
        _env_float(f"{ENV_PREFIX}DISK_WARN", DEFAULT_DISK_WARN),
        _env_float(f"{ENV_PREFIX}DISK_CRIT", DEFAULT_DISK_CRIT),
    )


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def disk_info(
    repository: Path, history: list[dict[str, object]] | None = None, now: datetime | None = None
) -> dict[str, object] | None:
    """Espace libre et total du volume du dépôt, et jours restants au rythme récent."""
    try:
        usage = shutil.disk_usage(repository)
    except OSError:
        return None
    info: dict[str, object] = {
        "free": usage.free,
        "total": usage.total,
        "free_percent": round(100 * usage.free / usage.total, 1) if usage.total else 0.0,
    }
    days_left = _days_left(usage.free, history or [], now or datetime.now())
    if days_left is not None:
        info["days_left"] = days_left
    return info


def _days_left(free: int, history: list[dict[str, object]], now: datetime) -> int | None:
    points: list[tuple[datetime, int]] = []
    for run in history:
        size, started = run.get("repository_bytes"), run.get("started_at")
        if not isinstance(size, int) or not isinstance(started, str):
            continue
        try:
            moment = datetime.fromisoformat(started)
        except ValueError:
            continue
        if now - moment <= GROWTH_WINDOW:
            points.append((moment, size))
    if len(points) < 2:
        return None
    points.sort()
    days = (points[-1][0] - points[0][0]).total_seconds() / 86400
    growth = points[-1][1] - points[0][1]
    if days < 1 or growth <= 0:
        return None
    return int(free / (growth / days))


def evaluate(
    state: dict[str, object],
    now: datetime,
    grace: timedelta,
    disk: dict[str, object] | None = None,
) -> Verdict:
    """Règles du §3.1 de la spécification."""
    status = state.get("status")
    recorded = state.get("last_run")
    last: dict[str, object] = recorded if isinstance(recorded, dict) else {}
    warn, crit = disk_thresholds()
    free = disk.get("free_percent") if disk else None

    if status == "error":
        error = str(last.get("error") or "raison inconnue")
        kind = "corrupted" if error.startswith("dépôt corrompu") else "failed"
        return Verdict("error", kind, f"Dernière sauvegarde en échec : {error}")

    next_run = state.get("next_run")
    if isinstance(next_run, str) and status != "running":
        try:
            late = now > datetime.fromisoformat(next_run) + grace
        except ValueError:
            return Verdict(
                "error", "late", f"date de prochaine sauvegarde illisible : {next_run!r}"
            )
        if late:
            return Verdict("error", "late", f"Sauvegarde en retard : prévue à {next_run}")

    if isinstance(free, (int, float)) and free < crit:
        return Verdict("error", "disk_critical", f"Espace disque critique : {free} % libre")
    if isinstance(free, (int, float)) and free < warn:
        return Verdict("warning", "disk_low", f"Espace disque bas : {free} % libre")

    warnings = last.get("warnings")
    if isinstance(warnings, list) and warnings:
        return Verdict(
            "warning", "warnings", f"Dernière sauvegarde réussie, {len(warnings)} avertissement(s)"
        )
    if status == "pending":
        return Verdict("warning", "pending", f"En attente de la première sauvegarde ({next_run})")
    return Verdict("ok", "ok", "Tout va bien")
