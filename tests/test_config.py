from __future__ import annotations

from pathlib import Path

import pytest

from heaven.config import config_from_env, find_config, load_config
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


# -- configuration par l'environnement (mode conteneur) -------------------


def test_config_from_env_reads_a_container_style_environment() -> None:
    config = config_from_env(
        {
            "HEAVEN_SOURCES": "/sources/docs:/sources/photos",
            "HEAVEN_REPOSITORY": "/repository",
            "HEAVEN_EXCLUDES": "*.tmp, node_modules",
            "HEAVEN_COMPRESS": "false",
            "HEAVEN_FOLLOW_SYMLINKS": "yes",
            "HEAVEN_KEEP_LAST": "7",
            "HEAVEN_KEEP_MONTHLY": "6",
        }
    )

    assert config is not None
    assert config.sources == [Path("/sources/docs"), Path("/sources/photos")]
    assert config.repository == Path("/repository")
    assert config.excludes == ["*.tmp", "node_modules"]
    assert config.compress is False
    assert config.follow_symlinks is True
    assert config.retention.keep_last == 7
    assert config.retention.keep_monthly == 6
    assert config.retention.keep_daily == 0


def test_config_from_env_is_absent_when_nothing_is_set() -> None:
    assert config_from_env({}) is None
    assert config_from_env({"PATH": "/usr/bin"}) is None


@pytest.mark.parametrize(
    "env",
    [
        {"HEAVEN_SOURCES": "/sources"},
        {"HEAVEN_REPOSITORY": "/repository"},
        {"HEAVEN_SOURCES": "/s", "HEAVEN_REPOSITORY": "/r", "HEAVEN_KEEP_LAST": "sept"},
        {"HEAVEN_SOURCES": "/s", "HEAVEN_REPOSITORY": "/r", "HEAVEN_KEEP_LAST": "-1"},
        {"HEAVEN_SOURCES": "/s", "HEAVEN_REPOSITORY": "/r", "HEAVEN_COMPRESS": "peut-être"},
    ],
)
def test_config_from_env_rejects_an_incomplete_environment(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        config_from_env(env)
