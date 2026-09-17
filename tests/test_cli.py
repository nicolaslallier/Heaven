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
