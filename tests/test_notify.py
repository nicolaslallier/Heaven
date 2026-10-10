from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from heaven import notify, status
from heaven.scheduler_time import parse_duration, previous_weekly

NOW = datetime(2026, 10, 12, 9, 0)  # un lundi
GRACE = timedelta(hours=1)


def _state(run_status: str = "ok", error: str | None = None, warnings: int = 0) -> dict:
    return {
        "status": run_status,
        "next_run": "2026-12-01T02:30:00",
        "last_run": {"status": run_status, "error": error, "warnings": ["w"] * warnings},
        "history": [],
    }


class Outbox:
    def __init__(self) -> None:
        self.emails: list[tuple[str, str]] = []
        self.pushes: list[tuple[str, int]] = []
        self.fail = False

    def email(self, _config: object, subject: str, body: str) -> None:
        if self.fail:
            raise OSError("smtp injoignable")
        self.emails.append((subject, body))

    def push(self, _config: object, title: str, _body: str, priority: int) -> None:
        if self.fail:
            raise OSError("ntfy injoignable")
        self.pushes.append((title, priority))


def _notifier(tmp_path: Path, outbox: Outbox, weekly: str = "") -> notify.Notifier:
    config = notify.NotifyConfig(email_to="n@example.net", ntfy_token="t", weekly=weekly)
    return notify.Notifier(
        config,
        tmp_path,
        GRACE,
        lambda _m: None,
        email=outbox.email,
        push=outbox.push,
        disk=lambda *_args: None,
    )


def test_evaluate_follows_the_rules_of_the_spec() -> None:
    assert status.evaluate(_state(), NOW, GRACE).status == "ok"
    assert status.evaluate(_state(warnings=2), NOW, GRACE).status == "warning"
    failed = status.evaluate(_state("error", "source absente"), NOW, GRACE)
    assert (failed.status, failed.kind) == ("error", "failed")
    corrupted = status.evaluate(_state("error", "dépôt corrompu : 1"), NOW, GRACE)
    assert corrupted.kind == "corrupted"
    late = status.evaluate(_state(), datetime(2026, 12, 1, 4, 0), GRACE)
    assert late.kind == "late"
    low = status.evaluate(_state(), NOW, GRACE, {"free_percent": 15.0})
    assert (low.status, low.kind) == ("warning", "disk_low")
    assert status.evaluate(_state(), NOW, GRACE, {"free_percent": 5.0}).status == "error"


def test_failure_notifies_once_then_recovery(tmp_path: Path) -> None:
    outbox, memory = Outbox(), {}
    notifier = _notifier(tmp_path, outbox)
    assert not notifier.check(_state(), memory, NOW)  # tout va bien : silence

    assert notifier.check(_state("error", "boum"), memory, NOW)
    assert len(outbox.emails) == 1 and outbox.pushes == [("Heaven : Échec de sauvegarde", 4)]

    # Deuxième cycle en échec dans les 24 h : rien de plus.
    notifier.check(_state("error", "boum"), memory, NOW + timedelta(hours=3))
    assert len(outbox.emails) == 1
    # Rappel après 24 h.
    notifier.check(_state("error", "boum"), memory, NOW + timedelta(hours=25))
    assert len(outbox.emails) == 2

    notifier.check(_state(), memory, NOW + timedelta(hours=26))
    assert outbox.emails[-1][0] == "Heaven : rétabli"
    assert outbox.pushes[-1][1] == 2


def test_warnings_are_email_only(tmp_path: Path) -> None:
    outbox, memory = Outbox(), {}
    _notifier(tmp_path, outbox).check(_state(warnings=1), memory, NOW)
    assert len(outbox.emails) == 1 and outbox.pushes == []


def test_send_failure_never_raises_and_is_retried_later(tmp_path: Path) -> None:
    outbox, memory = Outbox(), {}
    outbox.fail = True
    notifier = _notifier(tmp_path, outbox)
    notifier.check(_state("error", "boum"), memory, NOW)
    assert memory["last_result"] == "échec" and "kind" not in memory

    outbox.fail = False
    notifier.check(_state("error", "boum"), memory, NOW + timedelta(minutes=5))
    assert outbox.emails == []  # trop tôt pour réessayer
    notifier.check(_state("error", "boum"), memory, NOW + timedelta(minutes=20))
    assert len(outbox.emails) == 1


def test_weekly_summary_waits_for_the_first_monday_after_startup(tmp_path: Path) -> None:
    outbox, memory = Outbox(), {}
    notifier = _notifier(tmp_path, outbox, weekly="mon@08:00")
    notifier.check(_state(), memory, NOW)  # premier démarrage : pas de résumé immédiat
    assert outbox.emails == []
    notifier.check(_state(), memory, NOW + timedelta(days=7))
    assert outbox.emails[0][0] == "Heaven : résumé de la semaine"


def test_disabled_notifier_does_nothing(tmp_path: Path) -> None:
    notifier = notify.Notifier(notify.NotifyConfig(), tmp_path, GRACE, lambda _m: None)
    assert not notifier.enabled and not notifier.check(_state("error", "x"), {}, NOW)


def test_helpers() -> None:
    assert parse_duration("24h") == timedelta(hours=24)
    assert previous_weekly("mon@08:00", NOW) == datetime(2026, 10, 12, 8, 0)
    assert previous_weekly("mon@10:00", NOW) == datetime(2026, 10, 5, 10, 0)
    assert notify.mask_address("nicolas@famillelallier.net") == "n***@famillelallier.net"


def test_failed_reminder_is_not_retried_every_tick(tmp_path: Path) -> None:
    outbox, memory = Outbox(), {}
    notifier = _notifier(tmp_path, outbox)
    notifier.check(_state("error", "boum"), memory, NOW)
    outbox.fail = True
    later = NOW + timedelta(hours=25)
    notifier.check(_state("error", "boum"), memory, later)  # rappel dû, relais en panne
    outbox.fail = False
    notifier.check(_state("error", "boum"), memory, later + timedelta(minutes=1))
    assert len(outbox.emails) == 1  # pas de nouvelle tentative avant le délai
    notifier.check(_state("error", "boum"), memory, later + timedelta(minutes=16))
    assert len(outbox.emails) == 2


def test_weekly_needs_email_and_tolerates_a_garbled_timestamp(tmp_path: Path) -> None:
    outbox = Outbox()
    only_push = notify.Notifier(
        notify.NotifyConfig(ntfy_token="t", weekly="mon@08:00"),
        tmp_path, GRACE, lambda _m: None, email=outbox.email, push=outbox.push,
        disk=lambda *_a: None,
    )  # fmt: skip
    memory: dict = {}
    only_push.check(_state(), memory, NOW + timedelta(days=8))
    assert "last_result" not in memory and outbox.pushes == []

    notifier = _notifier(tmp_path, outbox, weekly="mon@08:00")
    memory = {"weekly_at": "garbage"}
    notifier.check(_state(), memory, NOW)  # ne plante pas
    assert memory["weekly_at"] == "2026-10-12T08:00:00"


def test_corruption_stays_red_until_a_verification_passes() -> None:
    state = _state()
    state["last_verify"] = {"ok": False}
    assert status.evaluate(state, NOW, GRACE).kind == "corrupted"
    state["last_verify"] = {"ok": True}
    assert status.evaluate(state, NOW, GRACE).status == "ok"
