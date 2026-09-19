from __future__ import annotations

from pathlib import Path

import pytest

from heaven.cli import main
from heaven.config import Config


def test_init_writes_config_and_repository(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config_path = tmp_path / "heaven.toml"
    code = main(
        [
            "init",
            "--config-path",
            str(config_path),
            "--repository",
            str(tmp_path / "repo"),
        ]
    )
    assert code == 0
    assert config_path.is_file()
    assert (tmp_path / "repo" / "config.json").is_file()
    assert "configuration créée" in capsys.readouterr().out


def test_backup_then_restore_via_cli(
    tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repository = tmp_path / "repo"
    assert main(["backup", "--source", str(tree), "--repository", str(repository)]) == 0
    assert "instantané" in capsys.readouterr().out

    target = tmp_path / "out"
    assert main(["restore", "--repository", str(repository), "--target", str(target)]) == 0
    assert (target / "data" / "docs" / "a.txt").read_text(encoding="utf-8") == "contenu a\n"


def test_cli_uses_config_file(tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "heaven.toml"
    config_path.write_text(
        f'[backup]\nsources = ["{tree}"]\nrepository = "{tmp_path / "repo"}"\n'
        'excludes = ["*.log"]\n\n[retention]\nkeep_last = 1\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert main(["backup"]) == 0
    assert main(["backup"]) == 0
    assert main(["snapshots"]) == 0
    assert main(["prune"]) == 0
    assert main(["verify"]) == 0


def test_verify_returns_error_code_on_corruption(config: Config, tmp_path: Path) -> None:
    from heaven import engine
    from heaven.repository import Repository

    engine.backup(config)
    repository = Repository.open(config.repository)
    path = repository.object_path(next(iter(repository.iter_objects())))
    assert path is not None
    path.write_bytes(b"pas du gzip valide")

    assert main(["verify", "--repository", str(config.repository)]) == 2


def test_missing_config_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("heaven.cli.find_config", lambda: None)
    assert main(["snapshots"]) == 1


def test_backup_requires_repository_with_source(tree: Path) -> None:
    assert main(["backup", "--source", str(tree)]) == 1


def test_list_shows_snapshot_entries(
    tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repository = tmp_path / "repo"
    main(["backup", "--source", str(tree), "--repository", str(repository)])
    capsys.readouterr()

    assert main(["list", "--repository", str(repository)]) == 0
    out = capsys.readouterr().out
    assert "data/docs/a.txt" in out


# -- mode conteneur -------------------------------------------------------


def test_cli_falls_back_to_the_environment(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans heaven.toml, la pile Docker se configure par variables d'environnement."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("heaven.cli.find_config", lambda: None)
    monkeypatch.setenv("HEAVEN_SOURCES", str(tree))
    monkeypatch.setenv("HEAVEN_REPOSITORY", str(tmp_path / "repo"))
    monkeypatch.setenv("HEAVEN_EXCLUDES", "*.log")

    assert main(["backup"]) == 0
    assert main(["snapshots"]) == 0
    assert (tmp_path / "repo" / "config.json").is_file()


def test_cli_prefers_the_config_file_named_by_heaven_config(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "ailleurs" / "heaven.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f'[backup]\nsources = ["{tree}"]\nrepository = "{tmp_path / "repo"}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HEAVEN_CONFIG", str(config_path))

    assert main(["backup"]) == 0
    assert (tmp_path / "repo" / "config.json").is_file()


def test_serve_once_then_health(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("HEAVEN_STATE", str(state_path))

    code = main(
        [
            "serve",
            "--once",
            "--schedule",
            "6h",
            "--repository",
            str(tmp_path / "repo"),
            "--source",
            str(tree),
            "--state-file",
            str(state_path),
        ]
    )
    assert code == 0
    assert state_path.is_file()

    capsys.readouterr()
    assert main(["health"]) == 0
    assert "prochaine sauvegarde" in capsys.readouterr().out


def test_health_without_state_is_unhealthy(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["health", "--state-file", str(tmp_path / "absent.json")]) == 1
    assert "aucun état" in capsys.readouterr().err


def test_read_commands_need_only_the_repository_in_the_environment(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un conteneur de restauration n'a pas de sources : HEAVEN_REPOSITORY suffit."""
    repository = tmp_path / "repo"
    main(["backup", "--source", str(tree), "--repository", str(repository)])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("heaven.cli.find_config", lambda: None)
    monkeypatch.setenv("HEAVEN_REPOSITORY", str(repository))

    assert main(["snapshots"]) == 0
    assert main(["verify"]) == 0
    assert main(["restore", "--target", str(tmp_path / "out")]) == 0
