from __future__ import annotations

import os
from pathlib import Path

import pytest

from heaven import scanner
from heaven.scanner import is_excluded, scan, source_roots


def test_scan_prefixes_paths_with_source_name(tree: Path) -> None:
    paths = {entry.path for entry in scan([tree])}
    assert "data/docs/a.txt" in paths
    assert "data/docs" in paths


def test_scan_applies_excludes(tree: Path) -> None:
    paths = {entry.path for entry in scan([tree], excludes=["*.log", "cache"])}
    assert "data/notes.log" not in paths
    assert not any(path.startswith("data/cache") for path in paths)
    assert "data/docs/a.txt" in paths


def test_scan_records_metadata(tree: Path) -> None:
    entries = {entry.path: entry for entry in scan([tree])}
    entry = entries["data/docs/a.txt"]
    assert entry.kind == "file"
    assert entry.size == len("contenu a\n")
    assert entries["data/docs"].kind == "dir"


def test_scan_handles_single_file_source(tmp_path: Path) -> None:
    target = tmp_path / "seul.txt"
    target.write_text("x", encoding="utf-8")
    entries = list(scan([target]))
    assert [entry.path for entry in entries] == ["seul.txt"]


def test_scan_records_symlinks_without_following(tmp_path: Path) -> None:
    source = tmp_path / "s"
    source.mkdir()
    (source / "reel.txt").write_text("réel", encoding="utf-8")
    (source / "lien.txt").symlink_to("reel.txt")
    entries = {entry.path: entry for entry in scan([source])}
    assert entries["s/lien.txt"].kind == "symlink"
    assert entries["s/lien.txt"].target == "reel.txt"


def test_source_roots_disambiguates_identical_names(tmp_path: Path) -> None:
    first = tmp_path / "a" / "data"
    second = tmp_path / "b" / "data"
    assert sorted(source_roots([first, second]).values()) == ["data", "data-2"]


def test_is_excluded_matches_basename_or_full_path() -> None:
    assert is_excluded("var/app.log", ["*.log"])
    assert is_excluded("var/cache/x", ["cache"])
    assert is_excluded("data/build/out", ["data/build"])
    assert not is_excluded("var/app.txt", ["*.log"])
    assert not is_excluded("data/builder/out", ["data/build"])


def test_scan_skips_fifo_and_reports_it(tmp_path: Path) -> None:
    source = tmp_path / "s"
    source.mkdir()
    (source / "ok.txt").write_text("x", encoding="utf-8")
    os.mkfifo(source / "tube")
    skipped: list[tuple[str, str]] = []

    paths = [entry.path for entry in scan([source], skipped=skipped)]

    assert "s/tube" not in paths and "s/ok.txt" in paths
    assert [path for path, _ in skipped] == ["s/tube"]


def test_scan_survives_directory_vanishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "s"
    (source / "d").mkdir(parents=True)
    (source / "f.txt").write_text("x", encoding="utf-8")
    real = scanner._entry_for

    def flaky(path: Path, relative: str) -> scanner.FileEntry | None:
        if path.name == "d":
            raise FileNotFoundError(path)
        return real(path, relative)

    monkeypatch.setattr(scanner, "_entry_for", flaky)

    assert [entry.path for entry in scan([source])] == ["s/f.txt"]


def test_scan_follow_symlinks_does_not_loop(tmp_path: Path) -> None:
    source = tmp_path / "s"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "f.txt").write_text("x", encoding="utf-8")
    (source / "sub" / "boucle").symlink_to(source)  # lien vers un ancêtre
    (source / "autre").symlink_to(source / "sub")  # lien non cyclique : on y descend

    paths = [entry.path for entry in scan([source], follow_symlinks=True)]

    assert "s/autre/f.txt" in paths
    assert "s/sub/boucle" in paths  # gardé comme lien
    assert not any(path.startswith("s/sub/boucle/") for path in paths)
