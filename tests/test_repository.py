from __future__ import annotations

from pathlib import Path

import pytest

from heaven.errors import RepositoryError, SnapshotNotFoundError
from heaven.hashing import hash_file
from heaven.manifest import Snapshot
from heaven.repository import Repository


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    first = Repository.initialize(tmp_path / "repo")
    second = Repository.initialize(tmp_path / "repo")
    assert first.path == second.path
    assert (first.path / "config.json").is_file()


def test_initialize_refuses_non_empty_directory(tmp_path: Path) -> None:
    directory = tmp_path / "occupe"
    directory.mkdir()
    (directory / "fichier").write_text("x", encoding="utf-8")
    with pytest.raises(RepositoryError, match="n'est pas un dépôt"):
        Repository.initialize(directory)


def test_open_reports_missing_repository(tmp_path: Path) -> None:
    with pytest.raises(RepositoryError, match="introuvable"):
        Repository.open(tmp_path / "absent")


def test_add_object_deduplicates(tmp_path: Path) -> None:
    repository = Repository.initialize(tmp_path / "repo")
    payload = tmp_path / "f.bin"
    payload.write_bytes(b"contenu" * 100)
    digest = hash_file(payload)

    first = repository.add_object(payload, digest)
    second = repository.add_object(payload, digest)

    assert first > 0
    assert second == 0
    assert repository.has_object(digest)
    assert list(repository.iter_objects()) == [digest]


@pytest.mark.parametrize("compress", [True, False])
def test_object_roundtrip(tmp_path: Path, compress: bool) -> None:
    repository = Repository.initialize(tmp_path / "repo")
    payload = tmp_path / "f.bin"
    payload.write_bytes(b"des donnees compressibles " * 50)
    digest = hash_file(payload)
    repository.add_object(payload, digest, compress=compress)

    with repository.open_object(digest) as reader:
        assert reader.read() == payload.read_bytes()


def test_remove_object_frees_space(tmp_path: Path) -> None:
    repository = Repository.initialize(tmp_path / "repo")
    payload = tmp_path / "f.bin"
    payload.write_bytes(b"x" * 1000)
    digest = hash_file(payload)
    repository.add_object(payload, digest)

    assert repository.remove_object(digest) > 0
    assert not repository.has_object(digest)
    assert repository.remove_object(digest) == 0


def test_snapshot_roundtrip_and_resolution(tmp_path: Path) -> None:
    repository = Repository.initialize(tmp_path / "repo")
    older = Snapshot(id="20260101T000000.000Z", created_at="2026-01-01T00:00:00+00:00")
    newer = Snapshot(id="20260202T000000.000Z", created_at="2026-02-02T00:00:00+00:00", tag="soir")
    repository.save_snapshot(older)
    repository.save_snapshot(newer)

    assert repository.list_snapshot_ids() == [older.id, newer.id]
    assert repository.resolve_snapshot_id("latest") == newer.id
    assert repository.resolve_snapshot_id("20260101") == older.id
    assert repository.load_snapshot(newer.id).tag == "soir"


def test_resolve_snapshot_id_rejects_ambiguous_prefix(tmp_path: Path) -> None:
    repository = Repository.initialize(tmp_path / "repo")
    repository.save_snapshot(Snapshot(id="20260101T000000.000Z", created_at="2026-01-01T00:00:00Z"))
    repository.save_snapshot(Snapshot(id="20260101T111111.000Z", created_at="2026-01-01T11:11:11Z"))
    with pytest.raises(SnapshotNotFoundError, match="ambigu"):
        repository.resolve_snapshot_id("20260101")


def test_load_snapshot_rejects_unknown_format(tmp_path: Path) -> None:
    repository = Repository.initialize(tmp_path / "repo")
    path = repository.snapshot_path("20260101T000000.000Z")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"format": 99, "id": "x", "created_at": "y"}', encoding="utf-8")
    with pytest.raises(RepositoryError, match="format d'instantané"):
        repository.load_snapshot("20260101T000000.000Z")
