from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from heaven.config import Config
from heaven.errors import ConfigError, HeavenError
from heaven.repository import Repository
from heaven.scheduler import health, parse_schedule, run_cycle, serve


def test_parse_schedule_accepts_intervals() -> None:
    assert parse_schedule("6h").interval == timedelta(hours=6)
    assert parse_schedule("90m").interval == timedelta(minutes=90)
    assert parse_schedule("30 s").interval == timedelta(seconds=30)
    assert parse_schedule("1d").interval == timedelta(days=1)


def test_parse_schedule_accepts_daily_times() -> None:
    schedule = parse_schedule("daily@02:30")
    assert schedule.interval is None
    assert [moment.hour for moment in schedule.times] == [2]

    several = parse_schedule("14:00, 02:30")
    assert [(moment.hour, moment.minute) for moment in several.times] == [(2, 30), (14, 0)]


@pytest.mark.parametrize("spec", ["", "demain", "0h", "25:00", "02:75", "6x", "02h30"])
def test_parse_schedule_rejects_nonsense(spec: str) -> None:
    with pytest.raises(ConfigError):
        parse_schedule(spec)


def test_next_run_picks_the_next_daily_time() -> None:
    schedule = parse_schedule("02:30,14:00")
    assert schedule.next_run(datetime(2026, 5, 1, 9, 0)) == datetime(2026, 5, 1, 14, 0)
    # Après la dernière heure du jour, on bascule au lendemain.
    assert schedule.next_run(datetime(2026, 5, 1, 20, 0)) == datetime(2026, 5, 2, 2, 30)
    # Une échéance atteinte à la seconde près ne doit pas se redéclencher sur place.
    assert schedule.next_run(datetime(2026, 5, 1, 2, 30)) == datetime(2026, 5, 1, 14, 0)


def test_run_cycle_backs_up_prunes_and_verifies(config: Config, tmp_path: Path) -> None:
    config.retention.keep_last = 1
    from heaven import engine

    engine.backup(config)  # un instantané plus ancien, que la rétention doit retirer

    report = run_cycle(config, tag="conteneur", verify=True)

    assert report.status == "ok"
    assert report.verified
    assert report.files > 0
    assert report.snapshots_removed == 1
    snapshots = Repository.open(config.repository).list_snapshots()
    assert [snapshot.id for snapshot in snapshots] == [report.snapshot]
    assert snapshots[0].tag == "conteneur"


def test_run_cycle_reports_corruption_without_raising(config: Config) -> None:
    from heaven import engine

    engine.backup(config)
    repository = Repository.open(config.repository)
    path = repository.object_path(next(iter(repository.iter_objects())))
    assert path is not None
    path.write_bytes(b"pas du gzip valide")

    report = run_cycle(config, verify=True)

    assert report.status == "error"
    assert report.error is not None and "corrompu" in report.error


def test_run_cycle_refuses_to_back_up_a_missing_source(tmp_path: Path) -> None:
    """Volume non monté : pas d'instantané vide, et le cycle se termine sans exception."""
    config = Config(sources=[tmp_path / "absent"], repository=tmp_path / "repo")

    report = run_cycle(config)

    assert report.status == "error"
    assert report.error is not None and "aucune source accessible" in report.error
    assert report.finished_at is not None
    assert not (tmp_path / "repo").exists()


def test_run_cycle_warns_about_a_partially_missing_source(config: Config, tmp_path: Path) -> None:
    config.sources.append(tmp_path / "absente")

    report = run_cycle(config)

    assert report.status == "ok"
    assert any("source absente" in warning for warning in report.warnings)


def test_empty_backup_never_triggers_retention(tmp_path: Path) -> None:
    """Une source vidée ne doit pas faire disparaître les instantanés déjà en dépôt."""
    from heaven import engine

    source = tmp_path / "data"
    source.mkdir()
    (source / "a.txt").write_text("a\n", encoding="utf-8")
    config = Config(sources=[source], repository=tmp_path / "repo")
    config.retention.keep_last = 1
    engine.backup(config)
    (source / "a.txt").unlink()

    report = run_cycle(config)

    assert report.status == "ok"
    assert report.files == 0
    assert report.snapshots_removed == 0
    assert len(Repository.open(config.repository).list_snapshots()) == 2


def test_serve_runs_one_cycle_and_writes_its_state(config: Config, tmp_path: Path) -> None:
    state_path = tmp_path / "etat" / "state.json"
    lines: list[str] = []

    code = serve(
        config,
        parse_schedule("6h"),
        tag="conteneur",
        state_path=state_path,
        log=lines.append,
        max_cycles=1,
    )

    assert code == 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "ok"
    assert state["schedule"] == "6h"
    assert state["last_run"]["snapshot"]
    assert datetime.fromisoformat(state["next_run"]) > datetime.now()
    assert any("instantané" in line for line in lines)


def test_serve_returns_one_when_the_last_cycle_failed(tmp_path: Path) -> None:
    config = Config(sources=[tmp_path / "data"], repository=tmp_path / "repo")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("a\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    serve(config, parse_schedule("6h"), state_path=state_path, log=lambda _: None, max_cycles=1)

    # Le dépôt devient illisible entre deux cycles.
    repository = Repository.open(config.repository)
    path = repository.object_path(next(iter(repository.iter_objects())))
    assert path is not None
    path.write_bytes(b"pas du gzip valide")

    code = serve(
        config,
        parse_schedule("6h"),
        verify_every=1,
        state_path=state_path,
        log=lambda _: None,
        max_cycles=1,
    )
    assert code == 1


def test_health_reads_the_state_file(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "format": 1,
                "schedule": "6h",
                "status": "ok",
                "next_run": "2026-05-01T02:30:00",
                "last_run": {"snapshot": "20260430T023000.000Z", "status": "ok"},
            }
        ),
        encoding="utf-8",
    )

    healthy, reason = health(state_path, grace=timedelta(hours=1), now=datetime(2026, 5, 1, 1, 0))
    assert healthy
    assert "20260430T023000.000Z" in reason

    # Échéance dépassée au-delà de la tolérance : le conteneur devient « unhealthy ».
    late, reason = health(state_path, grace=timedelta(hours=1), now=datetime(2026, 5, 1, 4, 0))
    assert not late
    assert "retard" in reason


def test_health_fails_on_a_failed_cycle(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "format": 1,
                "status": "error",
                "next_run": "2999-01-01T02:30:00",
                "last_run": {"status": "error", "error": "dépôt corrompu"},
            }
        ),
        encoding="utf-8",
    )

    healthy, reason = health(state_path, grace=timedelta(hours=1))
    assert not healthy
    assert "dépôt corrompu" in reason


def test_health_without_state_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(HeavenError):
        health(tmp_path / "jamais-ecrit.json", grace=timedelta(hours=1))


def test_serve_prepares_the_repository_before_writing_its_state(
    config: Config, tmp_path: Path
) -> None:
    """Régression : un état rangé dans le dépôt rendait celui-ci « non vide ».

    `Repository.initialize` refuse alors d'écrire dans un répertoire qui contient
    déjà autre chose, et le service échouait dès son premier cycle.
    """
    state_path = config.repository / "state.json"
    lines: list[str] = []

    code = serve(
        config,
        parse_schedule("6h"),
        state_path=state_path,
        log=lines.append,
        max_cycles=1,
    )

    assert code == 0
    assert (config.repository / "config.json").is_file()
    assert state_path.is_file()
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "ok"


def test_serve_reports_an_unusable_repository_at_startup(tree: Path, tmp_path: Path) -> None:
    """Un dépôt pointant sur des données existantes est signalé au démarrage."""
    occupied = tmp_path / "pas-un-depot"
    occupied.mkdir()
    (occupied / "des-donnees.txt").write_text("déjà là\n", encoding="utf-8")
    config = Config(sources=[tree], repository=occupied)
    lines: list[str] = []

    code = serve(
        config,
        parse_schedule("6h"),
        state_path=tmp_path / "state.json",
        log=lines.append,
        max_cycles=1,
    )

    assert code == 1
    assert any("dépôt inutilisable" in line for line in lines)
    assert (occupied / "des-donnees.txt").read_text(encoding="utf-8") == "déjà là\n"
