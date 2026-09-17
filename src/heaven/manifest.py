"""Représentation sérialisable d'un instantané."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .errors import RepositoryError
from .scanner import FileEntry

SNAPSHOT_FORMAT = 1


@dataclass(slots=True)
class Snapshot:
    """Un instantané : la liste des fichiers sauvegardés et leurs empreintes."""

    id: str
    created_at: str
    entries: list[FileEntry] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)  # chemin -> empreinte
    sources: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    hostname: str = field(default_factory=socket.gethostname)
    tag: str | None = None

    @property
    def created(self) -> datetime:
        """Date de création, en UTC."""
        return datetime.fromisoformat(self.created_at)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.entries if entry.is_file)

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if entry.is_file)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": SNAPSHOT_FORMAT,
            "id": self.id,
            "created_at": self.created_at,
            "hostname": self.hostname,
            "tag": self.tag,
            "sources": self.sources,
            "excludes": self.excludes,
            "entries": [
                {
                    "path": entry.path,
                    "kind": entry.kind,
                    "size": entry.size,
                    "mtime": entry.mtime,
                    "mode": entry.mode,
                    "target": entry.target,
                    "hash": self.hashes.get(entry.path),
                }
                for entry in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Snapshot:
        version = raw.get("format")
        if version != SNAPSHOT_FORMAT:
            raise RepositoryError(
                f"format d'instantané non pris en charge : {version!r} (attendu {SNAPSHOT_FORMAT})"
            )
        entries: list[FileEntry] = []
        hashes: dict[str, str] = {}
        for item in raw.get("entries", []):
            entry = FileEntry(
                path=item["path"],
                kind=item["kind"],
                size=item.get("size", 0),
                mtime=item.get("mtime", 0.0),
                mode=item.get("mode", 0o644),
                target=item.get("target"),
            )
            entries.append(entry)
            if item.get("hash"):
                hashes[entry.path] = item["hash"]
        return cls(
            id=raw["id"],
            created_at=raw["created_at"],
            entries=entries,
            hashes=hashes,
            sources=raw.get("sources", []),
            excludes=raw.get("excludes", []),
            hostname=raw.get("hostname", ""),
            tag=raw.get("tag"),
        )


def new_snapshot_id(now: datetime | None = None) -> str:
    """Identifiant lisible et triable par ordre chronologique."""
    moment = now or datetime.now(UTC)
    return moment.strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
