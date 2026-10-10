"""Notifications : courriel (relais SMTP de l'Infra) et push ntfy.

Bibliothèque standard seulement. Un échec d'envoi n'arrête jamais le service :
il est journalisé et réessayé plus tard (contrat « au mieux » de l'Infra).
"""

from __future__ import annotations

import http.client
import json
import os
import smtplib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from . import status as status_module
from .config import ENV_PREFIX
from .scheduler_time import parse_duration, previous_weekly

Logger = Callable[[str], None]

RETRY_DELAY = timedelta(minutes=15)  # entre deux envois qui échouent
PUSH_KINDS = {"failed": 4, "late": 4, "corrupted": 5, "disk_critical": 4}  # priorité ntfy
LABELS = {
    "failed": "Échec de sauvegarde",
    "late": "Sauvegarde en retard",
    "corrupted": "Dépôt corrompu",
    "disk_critical": "Espace disque critique",
    "disk_low": "Espace disque bas",
    "warnings": "Avertissements de sauvegarde",
}


@dataclass(slots=True, frozen=True)
class NotifyConfig:
    smtp_host: str = "smtp"
    smtp_port: int = 25
    email_from: str = "notifications@famillelallier.net"
    email_to: str = ""
    ntfy_url: str = "http://ntfy"
    ntfy_token: str = ""
    ntfy_topic: str = "heaven-alertes"
    repeat: timedelta = timedelta(hours=24)
    weekly: str = "mon@08:00"

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_to)

    @property
    def push_enabled(self) -> bool:
        return bool(self.ntfy_token)

    @classmethod
    def from_env(cls) -> NotifyConfig:
        def get(name: str, default: str) -> str:
            return os.environ.get(f"{ENV_PREFIX}{name}", default).strip()

        try:
            port = int(get("SMTP_PORT", "25"))
        except ValueError:
            port = 25
        try:
            repeat = parse_duration(get("NOTIFY_REPEAT", "24h"))
        except ValueError:
            repeat = timedelta(hours=24)
        return cls(
            smtp_host=get("SMTP_HOST", "smtp"),
            smtp_port=port,
            email_from=get("NOTIFY_EMAIL_FROM", "notifications@famillelallier.net"),
            email_to=get("NOTIFY_EMAIL_TO", ""),
            ntfy_url=get("NTFY_URL", "http://ntfy").rstrip("/"),
            ntfy_token=get("NTFY_TOKEN", ""),
            ntfy_topic=get("NTFY_TOPIC", "heaven-alertes"),
            repeat=repeat,
            weekly=get("NOTIFY_WEEKLY", "mon@08:00"),
        )


def mask_address(address: str) -> str:
    """`nicolas@exemple.net` → `n***@exemple.net`."""
    name, _, domain = address.partition("@")
    return f"{name[:1]}***@{domain}" if domain else ""


# -- envoi ---------------------------------------------------------------


def send_email(config: NotifyConfig, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = config.email_from, config.email_to, subject
    message.set_content(body)
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=5) as smtp:
        smtp.send_message(message)


def send_push(config: NotifyConfig, title: str, body: str, priority: int, tags: str = "") -> None:
    payload = {
        "topic": config.ntfy_topic,
        "title": title,
        "message": body,
        "priority": priority,
        "tags": [tag for tag in tags.split(",") if tag],
    }
    request = urllib.request.Request(  # noqa: S310 - URL fournie par l'administrateur
        config.ntfy_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.ntfy_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5):  # noqa: S310
        pass


class Notifier:
    """Décide quand prévenir : au changement d'état, en rappel, et chaque semaine."""

    def __init__(
        self,
        config: NotifyConfig,
        repository: Path,
        grace: timedelta,
        log: Logger,
        email: Callable[[NotifyConfig, str, str], None] = send_email,
        push: Callable[..., None] = send_push,
        disk: Callable[..., dict[str, object] | None] = status_module.disk_info,
    ) -> None:
        self.config = config
        self.repository = repository
        self.grace = grace
        self.log = log
        self._email = email
        self._push = push
        self._disk = disk

    @property
    def enabled(self) -> bool:
        return self.config.email_enabled or self.config.push_enabled

    def check(self, state: dict[str, object], memory: dict[str, object], now: datetime) -> bool:
        """Envoie ce qui est dû ; `memory` est l'état persistant. True s'il a changé."""
        if not self.enabled:
            return False
        before = json.dumps(memory, sort_keys=True)
        history = state.get("history")
        disk = self._disk(self.repository, history if isinstance(history, list) else [], now)
        verdict = status_module.evaluate(state, now, self.grace, disk)
        kind = "ok" if verdict.kind in ("ok", "pending") else verdict.kind
        previous = str(memory.get("kind") or "ok")

        if not self._due(memory.get("retry_at"), timedelta(0), now):
            pass  # un envoi vient d'échouer : on laisse le relais respirer
        elif kind != previous:
            self._deliver(memory, verdict, kind, now, reminder=False, recovered_from=previous)
        elif kind != "ok" and self._due(memory.get("sent_at"), self.config.repeat, now):
            self._deliver(memory, verdict, kind, now, reminder=True)
        if self._due(memory.get("retry_at"), timedelta(0), now):
            self._weekly(state, memory, now)
        return json.dumps(memory, sort_keys=True) != before

    def _deliver(
        self,
        memory: dict[str, object],
        verdict: status_module.Verdict,
        kind: str,
        now: datetime,
        reminder: bool,
        recovered_from: str = "",
    ) -> None:
        recovery = kind == "ok"
        if recovery:
            title = "Heaven : rétabli"
            was = LABELS.get(recovered_from, recovered_from)
            body = f"Les sauvegardes sont de nouveau en bonne santé (après : {was})."
            priority = 2
        else:
            title = f"Heaven : {LABELS.get(kind, kind)}" + (" (rappel)" if reminder else "")
            body = verdict.reason
            priority = PUSH_KINDS.get(kind, 3)
        push = recovery or kind in PUSH_KINDS
        stamp = now.isoformat(timespec="seconds")
        outcomes = self._send(memory, title, body, priority if push else 0, now)
        if not outcomes:
            # Aucun canal sollicité (ex. « push seul » et un avertissement) : ce n'est
            # ni un envoi ni un échec. On mémorise l'état pour ne pas le rejouer.
            memory.update(kind=kind, sent_at=stamp)
            return
        memory["last_attempt"] = stamp
        memory["last_errors"] = [error for _channel, error in outcomes if error]
        if any(error is None for _channel, error in outcomes):
            memory.update(kind=kind, sent_at=stamp)
            memory["last_result"] = "ok" if all(e is None for _c, e in outcomes) else "partiel"
        else:
            memory["last_result"] = "échec"

    def _send(
        self, memory: dict[str, object], title: str, body: str, push_priority: int, now: datetime
    ) -> list[tuple[str, str | None]]:
        """Envoie sur chaque canal ; retourne (canal, erreur ou None)."""
        outcomes: list[tuple[str, str | None]] = []
        if self.config.email_enabled:
            outcomes.append(("courriel", self._try("courriel", self._email, title, body)))
        if self.config.push_enabled and push_priority:
            outcomes.append(("push", self._try("push", self._push, title, body, push_priority)))
        if outcomes and all(error for _channel, error in outcomes):
            memory["retry_at"] = (now + RETRY_DELAY).isoformat(timespec="seconds")
        else:
            memory.pop("retry_at", None)
        return outcomes

    def _try(self, channel: str, send: Callable[..., None], *args: object) -> str | None:
        try:
            send(self.config, *args)
        except (OSError, smtplib.SMTPException, http.client.HTTPException, ValueError) as error:
            self.log(f"attention : notification {channel} non envoyée : {error}")
            return str(error)
        return None

    @staticmethod
    def _due(moment: object, delay: timedelta, now: datetime) -> bool:
        if not isinstance(moment, str):
            return True
        try:
            return now - datetime.fromisoformat(moment) >= delay
        except ValueError:
            return True

    def _weekly(self, state: dict[str, object], memory: dict[str, object], now: datetime) -> None:
        # Le résumé est un courriel : sans courriel configuré, il n'y a rien à envoyer.
        if not self.config.weekly or not self.config.email_enabled:
            return
        try:
            due = previous_weekly(self.config.weekly, now)
        except ValueError as error:  # HEAVEN_NOTIFY_WEEKLY mal écrit : pas de résumé, pas de crash
            self.log(f"attention : résumé hebdomadaire désactivé : {error}")
            return
        try:
            last = (
                datetime.fromisoformat(str(memory["weekly_at"])) if "weekly_at" in memory else None
            )
        except ValueError:
            last = None
            memory.pop("weekly_at", None)
        stamp = due.isoformat(timespec="seconds")
        if last is None:
            memory["weekly_at"] = stamp  # pas de résumé au premier démarrage
            return
        if last >= due:
            return
        summary = weekly_summary(state, self.repository, now)
        outcomes = self._send(memory, "Heaven : résumé de la semaine", summary, 0, now)
        if any(error is None for _channel, error in outcomes):
            memory["weekly_at"] = stamp

    def test(self) -> list[str]:
        """Message d'essai sur chaque canal configuré ; retourne les erreurs."""
        errors: list[str | None] = []
        if self.config.email_enabled:
            errors.append(self._try("courriel", self._email, "Heaven : essai", "Message d'essai."))
        if self.config.push_enabled:
            errors.append(self._try("push", self._push, "Heaven : essai", "Message d'essai.", 3))
        return [error for error in errors if error]


def weekly_summary(state: dict[str, object], repository: Path, now: datetime) -> str:
    history = state.get("history")
    runs = [run for run in history if isinstance(run, dict)] if isinstance(history, list) else []
    week = [run for run in runs if _within(run.get("started_at"), now, timedelta(days=7))]
    failed = [run for run in week if run.get("status") == "error"]
    lines = [
        f"Cycles de la semaine : {len(week) - len(failed)} réussi(s), {len(failed)} en échec.",
        f"Instantanés créés : {sum(1 for run in week if run.get('snapshot'))}.",
    ]
    sizes = [
        run["repository_bytes"] for run in week if isinstance(run.get("repository_bytes"), int)
    ]
    if sizes:
        lines.append(
            f"Taille du dépôt : {_human(sizes[0])} (variation : {_human(sizes[0] - sizes[-1])})."
        )
    disk = status_module.disk_info(repository, runs, now)
    if disk:
        lines.append(f"Espace libre : {disk['free_percent']} %.")
    verify = state.get("last_verify")
    if isinstance(verify, dict):
        lines.append(
            f"Dernière vérification : {verify.get('at')} "
            f"({'ok' if verify.get('ok') else 'problème'})."
        )
    return "\n".join(lines)


def _within(value: object, now: datetime, span: timedelta) -> bool:
    try:
        return isinstance(value, str) and now - datetime.fromisoformat(value) <= span
    except ValueError:
        return False


def _human(size: int) -> str:
    value, sign = float(abs(size)), "-" if size < 0 else ""
    for unit in ("o", "Kio", "Mio", "Gio"):
        if value < 1024:
            return f"{sign}{value:.1f} {unit}"
        value /= 1024
    return f"{sign}{value:.1f} Tio"
