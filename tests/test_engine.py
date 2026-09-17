from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heaven import engine
from heaven.config import Config, RetentionPolicy
from heaven.errors import IntegrityError
from heaven.manifest import Snapshot
from heaven.repository import Repository


def test_backup_creates_snapshot_and_deduplicates(config: Config) -> None:
    result = engine.backup(config, tag="premier")

    paths = {entry.path for entry in result.snapshot.entries}
    assert "data/docs/a.txt" in paths
    assert "data/notes.log" not in paths  # exclu
    assert result.snapshot.tag == "premier"
    # a.txt et copie-de-a.txt ont le même contenu : un seul objet stocké.
    assert result.files_total == 3
    assert result.files_stored == 2


def test_second_backup_stores_only_changes(config: Config) -> None:
    engine.backup(config)
    (config.sources[0] / "docs" / "c.txt").write_text("nouveau\n", encoding="utf-8")

    second = engine.backup(config)

    assert second.files_total == 4
    assert second.files_stored == 1
    assert second.files_reused == 3
    assert len(Repository.open(config.repository).list_snapshot_ids()) == 2


def test_restore_reproduces_content_and_metadata(config: Config, tmp_path: Path) -> None:
    engine.backup(config)
    target = tmp_path / "restauration"

    result = engine.restore(config.repository, "latest", target)

    assert (target / "data" / "docs" / "a.txt").read_text(encoding="utf-8") == "contenu a\n"
    assert (target / "data" / "copie-de-a.txt").read_text(encoding="utf-8") == "contenu a\n"
    assert not (target / "data" / "notes.log").exists()
    assert result.files_restored == 3
    original = config.sources[0] / "docs" / "a.txt"
    restored = target / "data" / "docs" / "a.txt"
    assert int(restored.stat().st_mtime) == int(original.stat().st_mtime)


def test_restore_can_select_a_subtree(config: Config, tmp_path: Path) -> None:
    engine.backup(config)
    target = tmp_path / "partiel"

    engine.restore(config.repository, "latest", target, paths=["data/docs"])

    assert (target / "data" / "docs" / "b.txt").exists()
    assert not (target / "data" / "copie-de-a.txt").exists()


def test_restore_skips_existing_files_unless_overwrite(config: Config, tmp_path: Path) -> None:
    engine.backup(config)
    target = tmp_path / "cible"
    (target / "data" / "docs").mkdir(parents=True)
    existing = target / "data" / "docs" / "a.txt"
    existing.write_text("à conserver\n", encoding="utf-8")

    skipped = engine.restore(config.repository, "latest", target)
    assert existing.read_text(encoding="utf-8") == "à conserver\n"
    assert ("data/docs/a.txt", "existe déjà") in skipped.skipped

    engine.restore(config.repository, "latest", target, overwrite=True)
    assert existing.read_text(encoding="utf-8") == "contenu a\n"


def test_restore_recreates_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "s"
    source.mkdir()
    (source / "reel.txt").write_text("réel", encoding="utf-8")
    (source / "lien.txt").symlink_to("reel.txt")
    config = Config(sources=[source], repository=tmp_path / "repo")
    engine.backup(config)

    target = tmp_path / "out"
    engine.restore(config.repository, "latest", target)

    assert (target / "s" / "lien.txt").is_symlink()
    assert (target / "s" / "lien.txt").readlink().name == "reel.txt"


def test_verify_accepts_a_healthy_repository(config: Config) -> None:
    engine.backup(config)
    result = engine.verify(config.repository)
    assert result.ok
    assert result.objects_checked == 2


def test_verify_detects_corruption(config: Config) -> None:
    engine.backup(config)
    repository = Repository.open(config.repository)
    digest = next(iter(repository.iter_objects()))
    path = repository.object_path(digest)
    assert path is not None
    with gzip.open(path, "wb") as handle:
        handle.write(b"donnees falsifiees")

    result = engine.verify(config.repository)

    assert not result.ok
    assert result.corrupted


def test_verify_detects_missing_object(config: Config) -> None:
    engine.backup(config)
    repository = Repository.open(config.repository)
    repository.remove_object(next(iter(repository.iter_objects())))

    result = engine.verify(config.repository)

    assert result.missing
    assert not result.ok


def test_read_file_returns_content(config: Config) -> None:
    engine.backup(config)
    assert engine.read_file(config.repository, "latest", "data/docs/b.txt") == b"contenu b\n"


def test_read_file_rejects_unknown_path(config: Config) -> None:
    engine.backup(config)
    with pytest.raises(IntegrityError):
        engine.read_file(config.repository, "latest", "data/absent.txt")


def test_backup_skips_unreadable_file(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    real_hash = engine.hash_file

    def fake_hash(path: Path) -> str:
        if path.name == "b.txt":
            raise PermissionError("accès refusé")
        return real_hash(path)

    monkeypatch.setattr(engine, "hash_file", fake_hash)
    result = engine.backup(config)

    assert [path for path, _ in result.skipped] == ["data/docs/b.txt"]
    assert "data/docs/b.txt" not in result.snapshot.hashes


def test_prune_keeps_everything_with_empty_policy(config: Config) -> None:
    engine.backup(config)
    engine.backup(config)

    result = engine.prune(config.repository, RetentionPolicy(keep_last=0))

    assert result.removed == []
    assert len(result.kept) == 2


def test_prune_removes_old_snapshots_and_orphan_objects(config: Config) -> None:
    engine.backup(config)
    unique = config.sources[0] / "docs" / "unique.txt"
    unique.write_text("seulement dans le second\n", encoding="utf-8")
    engine.backup(config)
    unique.unlink()
    engine.backup(config)

    result = engine.prune(config.repository, RetentionPolicy(keep_last=1))

    assert len(result.kept) == 1
    assert len(result.removed) == 2
    assert result.objects_removed == 1  # le contenu de unique.txt n'est plus référencé
    assert result.bytes_freed > 0
    assert engine.verify(config.repository).ok


def test_prune_dry_run_changes_nothing(config: Config) -> None:
    engine.backup(config)
    engine.backup(config)
    before = Repository.open(config.repository).list_snapshot_ids()

    result = engine.prune(config.repository, RetentionPolicy(keep_last=1), dry_run=True)

    assert len(result.removed) == 1
    assert Repository.open(config.repository).list_snapshot_ids() == before


def test_select_snapshots_to_keep_applies_periods() -> None:
    base = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    snapshots = [
        Snapshot(
            id=f"s{index}",
            created_at=(base - timedelta(days=index)).isoformat(),
        )
        for index in range(40)
    ]

    kept = engine.select_snapshots_to_keep(
        snapshots, RetentionPolicy(keep_last=2, keep_daily=3, keep_weekly=2, keep_monthly=2)
    )
    kept_ids = {snapshot.id for snapshot in kept}

    assert {"s0", "s1"} <= kept_ids  # les deux plus récents
    assert "s2" in kept_ids  # troisième jour distinct
    assert len(kept_ids) < len(snapshots)


def test_restore_reports_unreadable_object(config: Config, tmp_path: Path) -> None:
    engine.backup(config)
    repository = Repository.open(config.repository)
    path = repository.object_path(next(iter(repository.iter_objects())))
    assert path is not None
    path.write_bytes(b"pas du gzip valide")

    with pytest.raises(IntegrityError, match="illisible"):
        engine.restore(config.repository, "latest", tmp_path / "out")
