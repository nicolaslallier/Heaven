"""Calcul d'empreintes en flux, pour ne jamais charger un fichier entier en mémoire."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024
ALGORITHM = "sha256"


def hash_stream(stream: BinaryIO) -> str:
    """Retourne l'empreinte hexadécimale du flux, lu depuis sa position courante."""
    digest = hashlib.new(ALGORITHM)
    while chunk := stream.read(CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def hash_file(path: Path) -> str:
    """Retourne l'empreinte hexadécimale du fichier."""
    with path.open("rb") as handle:
        return hash_stream(handle)
