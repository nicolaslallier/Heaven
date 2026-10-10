"""Parcours des sources et filtrage par motifs d'exclusion."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath


@dataclass(slots=True)
class FileEntry:
    """Un élément du système de fichiers tel qu'il apparaît dans un instantané.

    `path` est relatif à la racine de l'instantané, toujours en séparateurs POSIX,
    ce qui rend un instantané restaurable sur une autre plateforme.
    """

    path: str
    kind: str  # "file", "dir" ou "symlink"
    size: int = 0
    mtime: float = 0.0
    mode: int = 0o644
    target: str | None = None  # cible, pour un lien symbolique

    @property
    def is_file(self) -> bool:
        return self.kind == "file"


def is_excluded(relative_path: str, patterns: Sequence[str]) -> bool:
    """Vrai si le chemin (ou l'un de ses composants) correspond à un motif d'exclusion.

    Un motif sans séparateur est comparé à chaque composant du chemin : `*.log`
    exclut aussi `var/app.log`. Un motif avec séparateur est comparé au chemin complet.
    """
    if not patterns:
        return False
    parts = PurePosixPath(relative_path).parts
    for pattern in patterns:
        normalized = pattern.rstrip("/")
        if not normalized:
            continue
        if "/" in normalized:
            if fnmatch(relative_path, normalized) or relative_path.startswith(
                normalized.rstrip("*") + "/"
            ):
                return True
        elif any(fnmatch(part, normalized) for part in parts):
            return True
    return False


def source_roots(sources: Sequence[Path]) -> dict[Path, str]:
    """Associe chaque source à son préfixe dans l'instantané, en évitant les collisions."""
    roots: dict[Path, str] = {}
    used: set[str] = set()
    for source in sources:
        resolved = source.expanduser()
        base = resolved.name or "root"
        name = base
        counter = 2
        while name in used:
            name = f"{base}-{counter}"
            counter += 1
        used.add(name)
        roots[resolved] = name
    return roots


def scan(
    sources: Sequence[Path],
    excludes: Sequence[str] = (),
    follow_symlinks: bool = False,
    skipped: list[tuple[str, str]] | None = None,
) -> Iterator[FileEntry]:
    """Énumère le contenu des sources sous forme d'entrées d'instantané, triées par chemin.

    Les éléments qui ne sont ni fichier, ni dossier, ni lien (FIFO, socket, périphérique)
    sont ignorés ; si `skipped` est fourni, on y ajoute (chemin, raison) pour les signaler.
    """
    entries: list[FileEntry] = []
    ignored = skipped if skipped is not None else []
    for source, prefix in source_roots(sources).items():
        entries.extend(_scan_source(source, prefix, excludes, follow_symlinks, ignored))
    entries.sort(key=lambda entry: entry.path)
    yield from entries


def _scan_source(
    source: Path,
    prefix: str,
    excludes: Sequence[str],
    follow_symlinks: bool,
    skipped: list[tuple[str, str]],
) -> Iterator[FileEntry]:
    if source.is_file() and not source.is_symlink():
        if not is_excluded(prefix, excludes):
            entry = _checked_entry(source, prefix, skipped)
            if entry is not None:
                yield entry
        return

    # Avec follow_symlinks, ancêtres (st_dev, st_ino) de chaque dossier parcouru : un lien
    # vers l'un d'eux formerait une boucle, on le garde comme entrée sans y descendre.
    ancestors: dict[str, frozenset[tuple[int, int]]] = {}
    for dirpath, dirnames, filenames in os.walk(source, followlinks=follow_symlinks):
        directory = Path(dirpath)
        relative_dir = _relative(directory, source, prefix)
        if follow_symlinks and dirpath not in ancestors:
            ancestors[dirpath] = frozenset({_identity(directory)})

        # Élaguer l'arborescence en place : os.walk ne descend pas dans les répertoires retirés.
        kept = []
        for name in sorted(dirnames):
            child = f"{relative_dir}/{name}" if relative_dir else name
            if is_excluded(child, excludes):
                continue
            try:
                entry = _entry_for(directory / name, child)
            except OSError:
                continue  # dossier disparu pendant le parcours : on l'ignore
            yield entry
            if follow_symlinks:
                try:
                    identity = _identity(directory / name)
                except OSError:
                    continue
                if identity in ancestors[dirpath]:
                    continue  # boucle de liens : ne pas descendre
                ancestors[os.path.join(dirpath, name)] = ancestors[dirpath] | {identity}
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            child = f"{relative_dir}/{name}" if relative_dir else name
            if is_excluded(child, excludes):
                continue
            entry = _checked_entry(directory / name, child, skipped)
            if entry is not None:
                yield entry


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _checked_entry(
    path: Path, relative_path: str, skipped: list[tuple[str, str]]
) -> FileEntry | None:
    try:
        entry = _entry_for(path, relative_path)
    except OSError:
        # Fichier disparu ou illisible pendant le parcours : on l'ignore.
        return None
    if entry is None:
        skipped.append((relative_path, "type non pris en charge (FIFO, socket ou périphérique)"))
    return entry


def _relative(path: Path, source: Path, prefix: str) -> str:
    relative = path.relative_to(source)
    parts = relative.parts
    return "/".join((prefix, *parts)) if parts else prefix


def _entry_for(path: Path, relative_path: str) -> FileEntry | None:
    """Décrit `path` sans suivre les liens ; None pour un type spécial (FIFO, socket, etc.)."""
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        # Voulu : un lien (vers un fichier ou un dossier) est stocké comme lien, même avec
        # follow_symlinks, qui ne fait que parcourir le contenu des dossiers liés.
        return FileEntry(
            path=relative_path,
            kind="symlink",
            mtime=info.st_mtime,
            mode=mode,
            target=os.readlink(path),
        )
    if stat.S_ISDIR(info.st_mode):
        return FileEntry(path=relative_path, kind="dir", mtime=info.st_mtime, mode=mode)
    if not stat.S_ISREG(info.st_mode):
        return None  # lire un FIFO bloquerait indéfiniment
    return FileEntry(
        path=relative_path,
        kind="file",
        size=info.st_size,
        mtime=info.st_mtime,
        mode=mode,
    )
