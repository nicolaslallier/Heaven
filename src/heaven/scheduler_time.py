"""Durées et horaires hebdomadaires (« 24h », « mon@08:00 ») pour les notifications."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_DURATION = re.compile(r"^(\d+)\s*(m|h|d)$", re.IGNORECASE)
_WEEKLY = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)@(\d{1,2}):(\d{2})$", re.IGNORECASE)
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_duration(spec: str) -> timedelta:
    match = _DURATION.match(spec.strip())
    if not match or int(match.group(1)) <= 0:
        raise ValueError(f"durée invalide : {spec!r} (attendu « 24h », « 90m », « 2d »)")
    return timedelta(**{_UNITS[match.group(2).lower()]: int(match.group(1))})


def previous_weekly(spec: str, now: datetime) -> datetime:
    """Dernière occurrence (≤ `now`) d'un horaire « jour@HH:MM »."""
    match = _WEEKLY.match(spec.strip())
    if not match or int(match.group(2)) > 23 or int(match.group(3)) > 59:
        raise ValueError(f"horaire hebdomadaire invalide : {spec!r} (attendu « mon@08:00 »)")
    target = _DAYS.index(match.group(1).lower())
    moment = now.replace(
        hour=int(match.group(2)), minute=int(match.group(3)), second=0, microsecond=0
    )
    moment -= timedelta(days=(moment.weekday() - target) % 7)
    return moment - timedelta(days=7) if moment > now else moment
