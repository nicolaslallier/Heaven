"""Opérations de sauvegarde : backup, restore, verify, prune."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .config import Config, RetentionPolicy
from .errors import IntegrityError
from .hashing import hash_file, hash_stream
from .manifest import Snapshot, new_snapshot_id
from .repository import Repository
from .scanner import FileEntry, scan, source_roots

ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class BackupResult:
    """Ce qu'a produit une sauvegarde."""

    snapshot: Snapshot
    files_total: int = 0
    files_stored: int = 0  # contenus réellement écrits (les autres étaient déjà là)
    bytes_scanned: int = 0
    bytes_stored: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (chemin, raison)

    @property
    def files_reused(self) -> int:
        return self.files_total - self.files_stored


@dataclass(slots=True)
class RestoreResult:
    files_restored: int = 0
    dirs_created: int = 0
    symlinks_created: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class VerifyResult:
    snapshots_checked: int = 0
    objects_checked: int = 0
    missing: list[tuple[str, str]] = field(default_factory=list)  # (instantané, chemin)
    corrupted: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.corrupted


@dataclass(slots=True)
class PruneResult:
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    objects_removed: int = 0
    bytes_freed: int = 0


def backup(
    config: Config,
    tag: str | None = None,
    progress: ProgressCallback | None = None,
) -> BackupResult:
    """Crée un instantané des sources de `config` dans son dépôt."""
    repository = Repository.initialize(config.repository)
    now = datetime.now(UTC)
    snapshot = Snapshot(
        id=_unique_snapshot_id(repository, now),
        created_at=now.isoformat(),
        sources=[str(source) for source in config.sources],
        excludes=list(config.excludes),
        tag=tag,
    )
    result = BackupResult(snapshot=snapshot)

    for entry in scan(config.sources, config.excludes, config.follow_symlinks):
        if not entry.is_file:
            snapshot.entries.append(entry)
            continue
        source_path = _absolute_path(entry, config.sources)
        try:
            digest = hash_file(source_path)
            stored = repository.add_object(source_path, digest, compress=config.compress)
        except OSError as exc:
            # Un fichier illisible ne doit pas faire échouer toute la sauvegarde.
            result.skipped.append((entry.path, str(exc)))
            continue
        snapshot.entries.append(entry)
        snapshot.hashes[entry.path] = digest
        result.files_total += 1
        result.bytes_scanned += entry.size
        if stored:
            result.files_stored += 1
            result.bytes_stored += stored
        if progress:
            progress(entry.path)

    repository.save_snapshot(snapshot)
    return result


def restore(
    repository_path: Path,
    snapshot_ref: str,
    target: Path,
    paths: Sequence[str] = (),
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
) -> RestoreResult:
    """Restaure un instantané (ou seulement `paths`) dans le répertoire `target`."""
    repository = Repository.open(repository_path)
    snapshot = repository.load_snapshot(snapshot_ref)
    target.mkdir(parents=True, exist_ok=True)
    result = RestoreResult()
    selected = _select_entries(snapshot.entries, paths)

    for entry in selected:
        destination = target / entry.path
        if entry.kind == "dir":
            destination.mkdir(parents=True, exist_ok=True)
            result.dirs_created += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if not overwrite:
                result.skipped.append((entry.path, "existe déjà"))
                continue
            destination.unlink()
        if entry.kind == "symlink":
            os.symlink(entry.target or "", destination)
            result.symlinks_created += 1
            continue

        digest = snapshot.hashes.get(entry.path)
        if digest is None:
            result.skipped.append((entry.path, "empreinte absente de l'instantané"))
            continue
        try:
            with repository.open_object(digest) as reader, destination.open("wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise IntegrityError(
                f"contenu illisible pour « {entry.path} » (instantané {snapshot.id}) : {exc}"
            ) from exc
        os.chmod(destination, entry.mode)
        os.utime(destination, (entry.mtime, entry.mtime))
        result.files_restored += 1
        if progress:
            progress(entry.path)

    # Les dates des répertoires sont appliquées en dernier : y écrire les remettrait à jour.
    for entry in sorted(
        (item for item in selected if item.kind == "dir"),
        key=lambda item: item.path,
        reverse=True,
    ):
        destination = target / entry.path
        if destination.is_dir():
            os.utime(destination, (entry.mtime, entry.mtime))

    return result


def verify(
    repository_path: Path,
    snapshot_ref: str | None = None,
    progress: ProgressCallback | None = None,
) -> VerifyResult:
    """Vérifie que chaque contenu référencé est présent et conforme à son empreinte."""
    repository = Repository.open(repository_path)
    snapshots = (
        [repository.load_snapshot(snapshot_ref)] if snapshot_ref else repository.list_snapshots()
    )
    result = VerifyResult(snapshots_checked=len(snapshots))
    verified: set[str] = set()

    for snapshot in snapshots:
        for path, digest in snapshot.hashes.items():
            if not repository.has_object(digest):
                result.missing.append((snapshot.id, path))
                continue
            if digest in verified:
                continue
            verified.add(digest)
            result.objects_checked += 1
            try:
                with repository.open_object(digest) as reader:
                    actual = hash_stream(reader)
            except OSError:
                # Objet illisible ou flux compressé tronqué : c'est une corruption.
                result.corrupted.append((snapshot.id, path))
                continue
            if actual != digest:
                result.corrupted.append((snapshot.id, path))
            if progress:
                progress(path)

    return result


def read_file(repository_path: Path, snapshot_ref: str, path: str) -> bytes:
    """Retourne le contenu d'un fichier d'un instantané, après contrôle d'intégrité."""
    repository = Repository.open(repository_path)
    snapshot = repository.load_snapshot(snapshot_ref)
    digest = snapshot.hashes.get(path)
    if digest is None:
        raise IntegrityError(f"« {path} » n'est pas un fichier de l'instantané {snapshot.id}")
    try:
        with repository.open_object(digest) as reader:
            payload = reader.read()
    except OSError as exc:
        raise IntegrityError(
            f"contenu illisible pour « {path} » (instantané {snapshot.id}) : {exc}"
        ) from exc
    if hashlib.sha256(payload).hexdigest() != digest:
        raise IntegrityError(f"contenu corrompu pour « {path} » (instantané {snapshot.id})")
    return payload


def prune(
    repository_path: Path,
    policy: RetentionPolicy,
    dry_run: bool = False,
) -> PruneResult:
    """Applique la politique de rétention, puis supprime les objets devenus orphelins."""
    repository = Repository.open(repository_path)
    snapshots = repository.list_snapshots()
    keep = select_snapshots_to_keep(snapshots, policy)
    keep_ids = {snapshot.id for snapshot in keep}
    result = PruneResult(
        kept=sorted(keep_ids),
        removed=[snapshot.id for snapshot in snapshots if snapshot.id not in keep_ids],
    )
    if dry_run:
        result.objects_removed, result.bytes_freed = _orphan_estimate(repository, keep)
        return result

    for snapshot_id in result.removed:
        repository.remove_snapshot(snapshot_id)

    referenced = repository.referenced_hashes()
    for digest in list(repository.iter_objects()):
        if digest in referenced:
            continue
        freed = repository.remove_object(digest)
        if freed:
            result.objects_removed += 1
            result.bytes_freed += freed
    return result


def select_snapshots_to_keep(
    snapshots: Sequence[Snapshot],
    policy: RetentionPolicy,
) -> list[Snapshot]:
    """Sélectionne les instantanés conservés : les N derniers, puis un par période.

    Une politique vide conserve tout : `prune` ne doit jamais tout supprimer par défaut.
    """
    if policy.is_empty():
        return list(snapshots)

    ordered = sorted(snapshots, key=lambda snapshot: snapshot.created_at, reverse=True)
    keep: dict[str, Snapshot] = {}

    for snapshot in ordered[: policy.keep_last]:
        keep[snapshot.id] = snapshot

    for count, period in (
        (policy.keep_daily, "%Y-%m-%d"),
        (policy.keep_weekly, "%G-W%V"),
        (policy.keep_monthly, "%Y-%m"),
    ):
        if count <= 0:
            continue
        seen: set[str] = set()
        for snapshot in ordered:
            bucket = snapshot.created.strftime(period)
            if bucket in seen:
                continue
            seen.add(bucket)
            keep[snapshot.id] = snapshot
            if len(seen) >= count:
                break

    return sorted(keep.values(), key=lambda snapshot: snapshot.created_at)


def _orphan_estimate(repository: Repository, keep: Iterable[Snapshot]) -> tuple[int, int]:
    referenced = {digest for snapshot in keep for digest in snapshot.hashes.values()}
    count = 0
    freed = 0
    for digest in repository.iter_objects():
        if digest in referenced:
            continue
        path = repository.object_path(digest)
        if path is not None:
            count += 1
            freed += path.stat().st_size
    return count, freed


def _select_entries(entries: Sequence[FileEntry], paths: Sequence[str]) -> list[FileEntry]:
    if not paths:
        return list(entries)
    wanted = [path.strip("/") for path in paths]
    return [
        entry
        for entry in entries
        if any(entry.path == item or entry.path.startswith(item + "/") for item in wanted)
    ]


def _absolute_path(entry: FileEntry, sources: Sequence[Path]) -> Path:
    """Retrouve le chemin réel d'une entrée depuis son chemin relatif à l'instantané."""
    prefix, _, remainder = entry.path.partition("/")
    for source, name in source_roots(sources).items():
        if name != prefix:
            continue
        return source / remainder if remainder else source
    raise IntegrityError(f"impossible de localiser la source de « {entry.path} »")


def _unique_snapshot_id(repository: Repository, now: datetime) -> str:
    snapshot_id = new_snapshot_id(now)
    existing = set(repository.list_snapshot_ids())
    suffix = 1
    candidate = snapshot_id
    while candidate in existing:
        candidate = f"{snapshot_id}-{suffix}"
        suffix += 1
    return candidate
