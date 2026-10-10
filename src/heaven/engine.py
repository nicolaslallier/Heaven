"""Opérations de sauvegarde : backup, restore, verify, prune."""

from __future__ import annotations

import gzip
import hashlib
import os
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import chunking
from .config import Config, RetentionPolicy
from .errors import IntegrityError, RepositoryError
from .hashing import ALGORITHM, CHUNK_SIZE
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
    files_cached: int = 0  # fichiers inchangés depuis le dernier instantané, non relus
    blocks_total: int = 0
    blocks_stored: int = 0
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
    corrupt_objects: set[str] = field(default_factory=set)  # empreintes d'objets corrompus
    objects_removed: list[str] = field(default_factory=list)  # supprimés par `repair`

    @property
    def ok(self) -> bool:
        return not self.missing and not self.corrupted


@dataclass(slots=True)
class PruneResult:
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    objects_removed: int = 0
    bytes_freed: int = 0


# Marge sous laquelle une date de modification est jugée trop proche de l'instantané
# précédent pour prouver que le fichier n'a pas changé depuis (même seconde, même mtime).
_RACY_WINDOW = 2.0


def backup(
    config: Config,
    tag: str | None = None,
    progress: ProgressCallback | None = None,
    full: bool = False,
) -> BackupResult:
    """Crée un instantané des sources de `config` dans son dépôt.

    Sauf avec `full`, un fichier dont la taille et la date de modification sont celles du
    dernier instantané n'est pas relu : on en reprend l'empreinte, si ses objets existent.
    """
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
    previous = None if full else _previous_snapshot(repository)
    previous_entries = {item.path: item for item in previous.entries} if previous else {}

    for entry in scan(
        config.sources, config.excludes, config.follow_symlinks, skipped=result.skipped
    ):
        if not entry.is_file:
            snapshot.entries.append(entry)
            continue
        source_path = _absolute_path(entry, config.sources)
        try:
            if previous is not None and _reuse_previous(
                repository, previous, previous_entries.get(entry.path), entry, snapshot
            ):
                result.files_cached += 1
                stored = 0
            elif config.chunking == "cdc" and entry.size > chunking.THRESHOLD:
                stored = _store_chunked(repository, source_path, entry, snapshot, config, result)
            else:
                # L'empreinte est calculée pendant l'écriture : le contenu stocké est celui haché.
                digest, stored = repository.add_object(source_path, compress=config.compress)
                snapshot.hashes[entry.path] = digest
        except OSError as exc:
            # Un fichier illisible ne doit pas faire échouer toute la sauvegarde.
            _forget(snapshot, entry.path)
            result.skipped.append((entry.path, str(exc)))
            continue
        snapshot.entries.append(entry)
        result.files_total += 1
        result.bytes_scanned += entry.size
        if stored:
            result.files_stored += 1
            result.bytes_stored += stored
        if progress:
            progress(entry.path)

    repository.save_snapshot(snapshot)
    return result


def _previous_snapshot(repository: Repository) -> Snapshot | None:
    ids = repository.list_snapshot_ids()
    if not ids:
        return None
    try:
        return repository.load_snapshot(ids[-1])
    except RepositoryError:
        return None  # un instantané illisible prive du cache, pas de la sauvegarde


def _reuse_previous(
    repository: Repository,
    previous: Snapshot,
    before: FileEntry | None,
    entry: FileEntry,
    snapshot: Snapshot,
) -> bool:
    """Reprend l'empreinte du dernier instantané si le fichier n'a visiblement pas changé."""
    digest = previous.hashes.get(entry.path)
    if digest is None:
        return False
    if before is None or before.size != entry.size or before.mtime != entry.mtime:
        return False
    if entry.mtime + _RACY_WINDOW > previous.created.timestamp():
        return False  # modifié peut-être après la lecture, sans que la date ne change
    digests = previous.object_digests(entry.path)
    if not all(repository.has_object(item) for item in digests):
        return False
    snapshot.hashes[entry.path] = digest
    if entry.path in previous.blocks:
        snapshot.blocks[entry.path] = list(previous.blocks[entry.path])
    return True


def _store_chunked(
    repository: Repository,
    source_path: Path,
    entry: FileEntry,
    snapshot: Snapshot,
    config: Config,
    result: BackupResult,
) -> int:
    """Découpe le fichier en blocs, stocke ceux qui manquent et note leur liste."""
    whole = hashlib.new(ALGORITHM)
    digests: list[str] = []
    written = 0
    with source_path.open("rb") as reader:
        for block in chunking.iter_chunks(reader):
            whole.update(block)
            digest = hashlib.new(ALGORITHM, block).hexdigest()
            digests.append(digest)
            stored = repository.add_bytes(block, digest, compress=config.compress)
            result.blocks_total += 1
            if stored:
                result.blocks_stored += 1
                written += stored
    snapshot.hashes[entry.path] = whole.hexdigest()
    snapshot.blocks[entry.path] = digests
    return written


def _forget(snapshot: Snapshot, path: str) -> None:
    snapshot.hashes.pop(path, None)
    snapshot.blocks.pop(path, None)


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

    root = target.resolve()
    for entry in selected:
        destination = target / entry.path
        # Une entrée dont la destination sortirait de `target` (.., chemin absolu, ou lien
        # symbolique restauré plus tôt) est ignorée et signalée, sans faire échouer le reste.
        if not _is_inside(root, destination, entry.kind == "dir"):
            result.skipped.append((entry.path, "chemin hors de la destination"))
            continue
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

        digests = snapshot.object_digests(entry.path)
        if not digests:
            result.skipped.append((entry.path, "empreinte absente de l'instantané"))
            continue
        try:
            with destination.open("wb") as writer:
                for digest in digests:
                    with repository.open_object(digest) as reader:
                        while chunk := reader.read(1024 * 1024):
                            writer.write(chunk)
        except (OSError, EOFError, zlib.error) as exc:
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
        if _is_inside(root, destination, True) and destination.is_dir():
            os.utime(destination, (entry.mtime, entry.mtime))

    return result


def _is_inside(root: Path, destination: Path, is_dir: bool) -> bool:
    """Vrai si l'écriture en `destination` reste sous `root` (déjà résolu).

    Pour un fichier ou un lien, on résout le parent seulement : le dernier composant est
    remplacé (jamais suivi). Un dossier, lui, est résolu en entier.
    """
    resolved = destination.resolve() if is_dir else destination.parent.resolve() / destination.name
    return resolved == root or resolved.is_relative_to(root)


def verify(
    repository_path: Path,
    snapshot_ref: str | None = None,
    progress: ProgressCallback | None = None,
    repair: bool = False,
) -> VerifyResult:
    """Vérifie que chaque contenu référencé est présent et conforme à son empreinte.

    Avec `repair`, les objets trouvés corrompus sont supprimés du dépôt (les instantanés
    restent) : la prochaine sauvegarde les réécrit si la source est intacte.
    """
    repository = Repository.open(repository_path)
    snapshots = (
        [repository.load_snapshot(snapshot_ref)] if snapshot_ref else repository.list_snapshots()
    )
    result = VerifyResult(snapshots_checked=len(snapshots))
    verified: set[str] = set()

    for snapshot in snapshots:
        for path, whole in snapshot.hashes.items():
            digests = snapshot.object_digests(path)
            if not all(repository.has_object(digest) for digest in digests):
                result.missing.append((snapshot.id, path))
                continue
            chunked = path in snapshot.blocks
            if not chunked and digests[0] in verified:
                if digests[0] in result.corrupt_objects:
                    result.corrupted.append((snapshot.id, path))
                continue
            if not _verify_file(repository, digests, whole, chunked, verified, result):
                result.corrupted.append((snapshot.id, path))
            if progress:
                progress(path)

    if repair:
        for digest in sorted(result.corrupt_objects):
            if repository.remove_object(digest):
                result.objects_removed.append(digest)
    return result


def _verify_file(
    repository: Repository,
    digests: list[str],
    whole: str,
    chunked: bool,
    verified: set[str],
    result: VerifyResult,
) -> bool:
    """Relit les objets d'un fichier ; pour un fichier découpé, contrôle aussi le tout."""
    total = hashlib.new(ALGORITHM)
    ok = True
    for digest in digests:
        try:
            with repository.open_object(digest) as reader:
                block = hashlib.new(ALGORITHM)
                while chunk := reader.read(1024 * 1024):
                    block.update(chunk)
                    total.update(chunk)
        except (EOFError, zlib.error, gzip.BadGzipFile):
            # Flux gzip tronqué (EOFError) ou invalide : objet corrompu, `repair` peut le retirer.
            result.corrupt_objects.add(digest)
            verified.add(digest)
            return False
        except OSError:
            # Erreur d'E/S ou de droits : peut-être passagère, donc corrompu pour le rapport
            # mais jamais supprimé par `repair`. Pas dans `verified` : chaque instantané qui le
            # référence doit le signaler.
            return False
        if digest not in verified:
            verified.add(digest)
            result.objects_checked += 1
        if block.hexdigest() != digest:
            result.corrupt_objects.add(digest)
            ok = False
    return ok and (not chunked or total.hexdigest() == whole)


def read_file(repository_path: Path, snapshot_ref: str, path: str) -> bytes:
    """Retourne le contenu d'un fichier d'un instantané, après contrôle d'intégrité."""
    repository = Repository.open(repository_path)
    snapshot = repository.load_snapshot(snapshot_ref)
    digest = snapshot.hashes.get(path)
    if digest is None:
        raise IntegrityError(f"« {path} » n'est pas un fichier de l'instantané {snapshot.id}")
    whole = hashlib.new(ALGORITHM)
    parts: list[bytes] = []
    try:
        for object_digest in snapshot.object_digests(path):
            with repository.open_object(object_digest) as reader:
                while chunk := reader.read(CHUNK_SIZE):
                    whole.update(chunk)
                    parts.append(chunk)
    except (OSError, EOFError, zlib.error) as exc:
        raise IntegrityError(
            f"contenu illisible pour « {path} » (instantané {snapshot.id}) : {exc}"
        ) from exc
    if whole.hexdigest() != digest:
        raise IntegrityError(f"contenu corrompu pour « {path} » (instantané {snapshot.id})")
    payload = b"".join(parts)
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
            bucket = snapshot.created.astimezone().strftime(period)  # heure locale
            if bucket in seen:
                continue
            seen.add(bucket)
            keep[snapshot.id] = snapshot
            if len(seen) >= count:
                break

    return sorted(keep.values(), key=lambda snapshot: snapshot.created_at)


def _orphan_estimate(repository: Repository, keep: Iterable[Snapshot]) -> tuple[int, int]:
    referenced = set().union(*(snapshot.referenced_digests() for snapshot in keep))
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
