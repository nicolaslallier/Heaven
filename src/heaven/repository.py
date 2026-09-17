"""Dépôt de sauvegarde : magasin d'objets adressé par contenu + instantanés."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from .errors import RepositoryError, SnapshotNotFoundError
from .hashing import ALGORITHM
from .manifest import Snapshot

REPOSITORY_FORMAT = 1
CONFIG_FILE = "config.json"
OBJECTS_DIR = "objects"
SNAPSHOTS_DIR = "snapshots"


class Repository:
    """Un répertoire contenant les objets (le contenu des fichiers) et les instantanés.

    Le contenu est adressé par son empreinte : deux fichiers identiques, dans un même
    instantané ou d'un instantané à l'autre, ne sont stockés qu'une fois. C'est ce qui
    rend les sauvegardes successives incrémentales sans avoir à comparer les dates.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.objects_dir = path / OBJECTS_DIR
        self.snapshots_dir = path / SNAPSHOTS_DIR

    # -- cycle de vie ----------------------------------------------------

    @classmethod
    def initialize(cls, path: Path) -> Repository:
        """Crée le dépôt s'il n'existe pas ; ne touche pas à un dépôt déjà valide."""
        repository = cls(path)
        config_path = path / CONFIG_FILE
        if config_path.exists():
            repository._read_config()
            return repository
        if path.exists() and any(path.iterdir()):
            raise RepositoryError(f"le répertoire existe et n'est pas un dépôt Heaven : {path}")
        repository.objects_dir.mkdir(parents=True, exist_ok=True)
        repository.snapshots_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            config_path,
            json.dumps({"format": REPOSITORY_FORMAT, "hash": ALGORITHM}, indent=2).encode(),
        )
        return repository

    @classmethod
    def open(cls, path: Path) -> Repository:
        """Ouvre un dépôt existant."""
        repository = cls(path)
        repository._read_config()
        return repository

    def _read_config(self) -> dict[str, object]:
        config_path = self.path / CONFIG_FILE
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RepositoryError(f"dépôt introuvable : {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise RepositoryError(f"dépôt corrompu ({config_path}) : {exc}") from exc
        if raw.get("format") != REPOSITORY_FORMAT:
            raise RepositoryError(
                f"format de dépôt non pris en charge : {raw.get('format')!r} "
                f"(attendu {REPOSITORY_FORMAT})"
            )
        return raw

    # -- objets ----------------------------------------------------------

    def _object_paths(self, digest: str) -> tuple[Path, Path]:
        directory = self.objects_dir / digest[:2]
        plain = directory / digest[2:]
        return plain, plain.with_name(plain.name + ".gz")

    def object_path(self, digest: str) -> Path | None:
        """Chemin de l'objet stocké, ou None s'il est absent."""
        plain, compressed = self._object_paths(digest)
        if plain.exists():
            return plain
        if compressed.exists():
            return compressed
        return None

    def has_object(self, digest: str) -> bool:
        return self.object_path(digest) is not None

    def add_object(self, source: Path, digest: str, compress: bool = True) -> int:
        """Stocke le contenu de `source` sous son empreinte.

        Retourne le nombre d'octets écrits dans le dépôt, ou 0 si l'objet y était déjà.
        """
        if self.has_object(digest):
            return 0
        plain, compressed = self._object_paths(digest)
        target = compressed if compress else plain
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader:
            _write_atomic_stream(target, reader, compress=compress)
        return target.stat().st_size

    @contextlib.contextmanager
    def open_object(self, digest: str) -> Iterator[BinaryIO]:
        """Ouvre l'objet en lecture, en décompressant à la volée si besoin."""
        path = self.object_path(digest)
        if path is None:
            raise RepositoryError(f"objet absent du dépôt : {digest}")
        with gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb") as handle:
            yield handle

    def iter_objects(self) -> Iterator[str]:
        """Énumère les empreintes de tous les objets stockés."""
        if not self.objects_dir.exists():
            return
        for prefix_dir in sorted(self.objects_dir.iterdir()):
            if not prefix_dir.is_dir():
                continue
            for object_path in sorted(prefix_dir.iterdir()):
                name = object_path.name.removesuffix(".gz")
                yield prefix_dir.name + name

    def remove_object(self, digest: str) -> int:
        """Supprime un objet et retourne les octets libérés (0 s'il était absent)."""
        path = self.object_path(digest)
        if path is None:
            return 0
        freed = path.stat().st_size
        path.unlink()
        with contextlib.suppress(OSError):
            path.parent.rmdir()  # ne réussit que si le répertoire est vide
        return freed

    # -- instantanés -----------------------------------------------------

    def snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshots_dir / f"{snapshot_id}.json"

    def save_snapshot(self, snapshot: Snapshot) -> Path:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = self.snapshot_path(snapshot.id)
        payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=False).encode("utf-8")
        _write_atomic(path, payload)
        return path

    def load_snapshot(self, snapshot_id: str) -> Snapshot:
        """Charge un instantané ; `snapshot_id` peut être un préfixe non ambigu ou `latest`."""
        resolved = self.resolve_snapshot_id(snapshot_id)
        path = self.snapshot_path(resolved)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SnapshotNotFoundError(f"instantané introuvable : {snapshot_id}") from exc
        except json.JSONDecodeError as exc:
            raise RepositoryError(f"instantané corrompu ({path}) : {exc}") from exc
        return Snapshot.from_dict(raw)

    def list_snapshot_ids(self) -> list[str]:
        """Identifiants des instantanés, du plus ancien au plus récent."""
        if not self.snapshots_dir.exists():
            return []
        return sorted(path.stem for path in self.snapshots_dir.glob("*.json"))

    def list_snapshots(self) -> list[Snapshot]:
        return [self.load_snapshot(snapshot_id) for snapshot_id in self.list_snapshot_ids()]

    def resolve_snapshot_id(self, reference: str) -> str:
        """Résout `latest` ou un préfixe d'identifiant vers un identifiant complet."""
        available = self.list_snapshot_ids()
        if not available:
            raise SnapshotNotFoundError("le dépôt ne contient aucun instantané")
        if reference in ("latest", "last"):
            return available[-1]
        if reference in available:
            return reference
        matches = [snapshot_id for snapshot_id in available if snapshot_id.startswith(reference)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SnapshotNotFoundError(f"instantané introuvable : {reference}")
        raise SnapshotNotFoundError(f"référence ambiguë « {reference} » : {', '.join(matches)}")

    def remove_snapshot(self, snapshot_id: str) -> None:
        resolved = self.resolve_snapshot_id(snapshot_id)
        self.snapshot_path(resolved).unlink(missing_ok=True)

    def referenced_hashes(self) -> set[str]:
        """Empreintes référencées par au moins un instantané."""
        referenced: set[str] = set()
        for snapshot in self.list_snapshots():
            referenced.update(snapshot.hashes.values())
        return referenced


def _write_atomic(path: Path, payload: bytes) -> None:
    """Écrit le fichier par renommage, pour qu'un lecteur ne voie jamais d'état partiel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_atomic_stream(path: Path, reader: BinaryIO, compress: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(descriptor, "wb") as raw:
            if compress:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as writer:
                    shutil.copyfileobj(reader, writer)
            else:
                shutil.copyfileobj(reader, raw)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
