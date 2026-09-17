from __future__ import annotations

from pathlib import Path

import pytest

from heaven.config import find_config, load_config
from heaven.errors import ConfigError


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "heaven.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_config_resolves_relative_paths(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        [backup]
        sources = ["data"]
        repository = "repo"
        excludes = ["*.log"]

        [retention]
        keep_last = 3
        keep_daily = 2
        """,
    )
    config = load_config(path)
    assert config.sources == [tmp_path / "data"]
    assert config.repository == tmp_path / "repo"
    assert config.excludes == ["*.log"]
    assert config.retention.keep_last == 3
    assert config.retention.keep_daily == 2
    assert config.compress is True


def test_load_config_accepts_single_source_as_string(tmp_path: Path) -> None:
    path = write(tmp_path, '[backup]\nsources = "data"\nrepository = "repo"\n')
    assert load_config(path).sources == [tmp_path / "data"]


def test_load_config_rejects_missing_repository(tmp_path: Path) -> None:
    path = write(tmp_path, '[backup]\nsources = ["data"]\n')
    with pytest.raises(ConfigError, match="repository"):
        load_config(path)


def test_load_config_rejects_empty_sources(tmp_path: Path) -> None:
    path = write(tmp_path, '[backup]\nsources = []\nrepository = "repo"\n')
    with pytest.raises(ConfigError, match="sources"):
        load_config(path)


def test_load_config_rejects_unknown_retention_key(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[backup]\nsources = ["d"]\nrepository = "r"\n\n[retention]\nkeep_yearly = 1\n',
    )
    with pytest.raises(ConfigError, match="keep_yearly"):
        load_config(path)


def test_load_config_rejects_negative_retention(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[backup]\nsources = ["d"]\nrepository = "r"\n\n[retention]\nkeep_last = -1\n',
    )
    with pytest.raises(ConfigError, match="positif"):
        load_config(path)


def test_load_config_reports_invalid_toml(tmp_path: Path) -> None:
    path = write(tmp_path, "[backup\n")
    with pytest.raises(ConfigError, match="invalide"):
        load_config(path)


def test_load_config_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(tmp_path / "absent.toml")


def test_find_config_walks_up_parents(tmp_path: Path) -> None:
    write(tmp_path, '[backup]\nsources = ["d"]\nrepository = "r"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == tmp_path / "heaven.toml"


def test_find_config_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_config(tmp_path) is None
