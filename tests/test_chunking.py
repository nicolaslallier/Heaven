from __future__ import annotations

import gzip
import io
import os
import random
from pathlib import Path

import pytest

from heaven import chunking, engine
from heaven.config import Config, ConfigError
from heaven.repository import Repository


def _payload(size: int, seed: int = 1) -> bytes:
    return random.Random(seed).randbytes(size)


def _chunks(data: bytes) -> list[bytes]:
    return list(chunking.iter_chunks(io.BytesIO(data)))


def test_chunks_reassemble_and_respect_bounds() -> None:
    data = _payload(chunking.MAX_SIZE * 3 + 12345)
    pieces = _chunks(data)

    assert b"".join(pieces) == data
    assert all(len(piece) <= chunking.MAX_SIZE for piece in pieces)
    assert all(len(piece) >= chunking.MIN_SIZE for piece in pieces[:-1])


def test_small_input_is_a_single_chunk_and_empty_input_none() -> None:
    assert _chunks(b"abc") == [b"abc"]
    assert _chunks(b"") == []


def test_boundaries_resynchronise_after_an_insertion() -> None:
    data = _payload(chunking.AVERAGE_SIZE * 8)
    edited = data[:400_000] + b"INSERTION" + data[400_000:]

    before = set(_chunks(data))
    after = _chunks(edited)

    shared = sum(1 for piece in after if piece in before)
    assert shared >= len(after) - 3  # seuls les blocs voisins de l'insertion changent


@pytest.fixture
def big_config(tmp_path: Path) -> Config:
    source = tmp_path / "data"
    source.mkdir()
    (source / "disque.img").write_bytes(_payload(chunking.AVERAGE_SIZE * 6))
    (source / "petit.txt").write_text("petit\n", encoding="utf-8")
    return Config(sources=[source], repository=tmp_path / "repo", chunking="cdc")


def test_invalid_chunking_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config(sources=[tmp_path], repository=tmp_path / "r", chunking="zip")


def test_large_file_is_chunked_and_small_file_is_not(big_config: Config) -> None:
    result = engine.backup(big_config)

    assert "data/disque.img" in result.snapshot.blocks
    assert "data/petit.txt" not in result.snapshot.blocks
    assert result.blocks_total == len(result.snapshot.blocks["data/disque.img"])


def test_restore_and_read_file_roundtrip_chunked(big_config: Config, tmp_path: Path) -> None:
    engine.backup(big_config)
    original = (big_config.sources[0] / "disque.img").read_bytes()

    engine.restore(big_config.repository, "latest", tmp_path / "out")

    assert (tmp_path / "out" / "data" / "disque.img").read_bytes() == original
    assert engine.read_file(big_config.repository, "latest", "data/disque.img") == original


def test_small_edit_stores_only_the_touched_blocks(big_config: Config) -> None:
    first = engine.backup(big_config)
    image = big_config.sources[0] / "disque.img"
    data = bytearray(image.read_bytes())
    data[1_000_000] ^= 0xFF
    image.write_bytes(bytes(data))

    second = engine.backup(big_config)

    assert second.blocks_stored <= 2
    assert second.bytes_stored < first.bytes_stored / 2


def test_verify_accepts_chunked_repository_and_detects_damage(big_config: Config) -> None:
    result = engine.backup(big_config)
    assert engine.verify(big_config.repository).ok

    repository = Repository.open(big_config.repository)
    digest = result.snapshot.blocks["data/disque.img"][0]
    path = repository.object_path(digest)
    assert path is not None
    with gzip.open(path, "wb") as handle:
        handle.write(b"abime")

    assert not engine.verify(big_config.repository).ok


def test_verify_detects_missing_block(big_config: Config) -> None:
    result = engine.backup(big_config)
    Repository.open(big_config.repository).remove_object(
        result.snapshot.blocks["data/disque.img"][1]
    )

    assert engine.verify(big_config.repository).missing


def test_prune_keeps_blocks_of_kept_snapshots(big_config: Config) -> None:
    from heaven.config import RetentionPolicy

    engine.backup(big_config)
    image = big_config.sources[0] / "disque.img"
    image.write_bytes(_payload(chunking.AVERAGE_SIZE * 4, seed=2))
    engine.backup(big_config)

    engine.prune(big_config.repository, RetentionPolicy(keep_last=1))

    assert engine.verify(big_config.repository).ok
    repository = Repository.open(big_config.repository)
    assert set(repository.iter_objects()) == repository.referenced_hashes()


def test_snapshot_without_blocks_stays_format_1(config: Config) -> None:
    result = engine.backup(config)

    assert result.snapshot.to_dict()["format"] == 1


# -- cache de métadonnées ---------------------------------------------------


def _age(path: Path) -> None:
    """Vieillit les fichiers pour sortir de la fenêtre où la date n'est pas probante."""
    for item in path.rglob("*"):
        if item.is_file():
            os.utime(item, (1_000_000, 1_000_000))


def test_unchanged_files_are_not_reread(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _age(config.sources[0])
    engine.backup(config)
    monkeypatch.setattr(engine, "hash_file", lambda path: pytest.fail("relu"))

    second = engine.backup(config)

    assert second.files_cached == second.files_total == 3
    assert second.files_stored == 0


def test_full_forces_a_reread(config: Config) -> None:
    _age(config.sources[0])
    engine.backup(config)

    assert engine.backup(config, full=True).files_cached == 0


def test_changed_file_is_detected_by_size(config: Config) -> None:
    _age(config.sources[0])
    engine.backup(config)
    target = config.sources[0] / "docs" / "a.txt"
    target.write_text("autre contenu, plus long\n", encoding="utf-8")
    os.utime(target, (1_000_000, 1_000_000))  # même date, taille différente

    second = engine.backup(config)

    assert second.files_cached == 2
    assert second.files_stored == 1


def test_recent_files_are_never_trusted(config: Config) -> None:
    engine.backup(config)  # fichiers créés à l'instant : date non probante

    assert engine.backup(config).files_cached == 0


def test_cache_is_ignored_when_an_object_is_missing(config: Config) -> None:
    _age(config.sources[0])
    first = engine.backup(config)
    Repository.open(config.repository).remove_object(first.snapshot.hashes["data/docs/b.txt"])

    second = engine.backup(config)

    assert second.files_cached == 2
    assert engine.verify(config.repository).ok
