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
) -> Iterator[FileEntry]:
    """Énumère le contenu des sources sous forme d'entrées d'instantané, triées par chemin."""
    entries: list[FileEntry] = []
    for source, prefix in source_roots(sources).items():
        entries.extend(_scan_source(source, prefix, excludes, follow_symlinks))
    entries.sort(key=lambda entry: entry.path)
    yield from entries


def _scan_source(
    source: Path,
    prefix: str,
    excludes: Sequence[str],
    follow_symlinks: bool,
) -> Iterator[FileEntry]:
    if source.is_file() and not source.is_symlink():
        if not is_excluded(prefix, excludes):
            yield _entry_for(source, prefix)
        return

    for dirpath, dirnames, filenames in os.walk(source, followlinks=follow_symlinks):
        directory = Path(dirpath)
        relative_dir = _relative(directory, source, prefix)

        # Élaguer l'arborescence en place : os.walk ne descend pas dans les répertoires retirés.
        kept = []
        for name in sorted(dirnames):
            child = f"{relative_dir}/{name}" if relative_dir else name
            if not is_excluded(child, excludes):
                kept.append(name)
                yield _entry_for(directory / name, child)
        dirnames[:] = kept

        for name in sorted(filenames):
            child = f"{relative_dir}/{name}" if relative_dir else name
            if is_excluded(child, excludes):
                continue
            path = directory / name
            try:
                yield _entry_for(path, child)
            except OSError:
                # Fichier disparu ou illisible pendant le parcours : on l'ignore.
                continue


def _relative(path: Path, source: Path, prefix: str) -> str:
    relative = path.relative_to(source)
    parts = relative.parts
    return "/".join((prefix, *parts)) if parts else prefix


def _entry_for(path: Path, relative_path: str) -> FileEntry:
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return FileEntry(
            path=relative_path,
            kind="symlink",
            mtime=info.st_mtime,
            mode=mode,
            target=os.readlink(path),
        )
    if stat.S_ISDIR(info.st_mode):
        return FileEntry(path=relative_path, kind="dir", mtime=info.st_mtime, mode=mode)
    return FileEntry(
        path=relative_path,
        kind="file",
        size=info.st_size,
        mtime=info.st_mtime,
        mode=mode,
    )
