"""Découpage en blocs à contenu défini (CDC, dans l'esprit de FastCDC).

Les frontières dépendent du contenu et non de la position : insérer ou retirer des
octets au milieu d'un fichier ne déplace que les blocs voisins, les suivants gardent
la même empreinte et sont donc dédupliqués. Le tableau « gear » est dérivé de SHA-256
pour rester identique d'une version à l'autre : le modifier changerait toutes les
frontières, donc casserait la déduplication avec les blocs déjà stockés.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import BinaryIO

AVERAGE_SIZE = 1024 * 1024
MIN_SIZE = AVERAGE_SIZE // 4
MAX_SIZE = AVERAGE_SIZE * 4
# En dessous, un fichier reste un seul objet : le découpage n'apporterait rien.
THRESHOLD = AVERAGE_SIZE

_BITS = AVERAGE_SIZE.bit_length() - 1
_MASK64 = (1 << 64) - 1
# Bits de poids fort : ils dépendent des derniers octets lus, contrairement aux bits faibles.
_CUT_MASK = ((1 << _BITS) - 1) << (64 - _BITS)
_READ_SIZE = 4 * MAX_SIZE


def _gear_table() -> tuple[int, ...]:
    return tuple(
        int.from_bytes(hashlib.sha256(b"heaven-gear-%d" % index).digest()[:8], "big")
        for index in range(256)
    )


_GEAR = _gear_table()


def iter_chunks(stream: BinaryIO) -> Iterator[bytes]:
    """Découpe le flux en blocs de MIN_SIZE à MAX_SIZE octets, selon son contenu.

    Débit : environ 15 Mo/s (boucle Python octet par octet, mesuré sur une entrée
    pseudo-aléatoire), donc le découpage borne la vitesse d'une sauvegarde `cdc`. Ne pas
    « optimiser » en changeant l'algorithme : les frontières (figées par
    `test_chunk_boundaries_are_frozen`) doivent rester identiques pour garder la
    déduplication avec les blocs déjà stockés.
    """
    gear = _GEAR
    buffer = b""
    start = 0
    exhausted = False
    while True:
        while len(buffer) - start < MAX_SIZE and not exhausted:
            more = stream.read(_READ_SIZE)
            if more:
                buffer = buffer[start:] + more
                start = 0
            else:
                exhausted = True
        available = len(buffer) - start
        if available == 0:
            return
        if available <= MIN_SIZE:
            yield buffer[start:]
            return

        limit = start + min(available, MAX_SIZE)
        cut = limit
        fingerprint = 0
        for index in range(start + MIN_SIZE, limit):
            fingerprint = ((fingerprint << 1) + gear[buffer[index]]) & _MASK64
            if not fingerprint & _CUT_MASK:
                cut = index + 1
                break
        yield buffer[start:cut]
        start = cut
