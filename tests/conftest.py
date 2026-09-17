from __future__ import annotations

from pathlib import Path

import pytest

from heaven.config import Config


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Une petite arborescence source, avec un doublon et de quoi tester les exclusions."""
    source = tmp_path / "data"
    (source / "docs").mkdir(parents=True)
    (source / "cache").mkdir()
    (source / "docs" / "a.txt").write_text("contenu a\n", encoding="utf-8")
    (source / "docs" / "b.txt").write_text("contenu b\n", encoding="utf-8")
    (source / "copie-de-a.txt").write_text("contenu a\n", encoding="utf-8")  # doublon exact
    (source / "notes.log").write_text("bruit\n", encoding="utf-8")
    (source / "cache" / "gros.tmp").write_text("jetable\n", encoding="utf-8")
    return source


@pytest.fixture
def config(tree: Path, tmp_path: Path) -> Config:
    return Config(
        sources=[tree],
        repository=tmp_path / "repo",
        excludes=["*.log", "cache"],
    )
