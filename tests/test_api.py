from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from heaven import api, engine
from heaven.config import Config


def _get(base: str, path: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(base + path) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_api_serves_snapshots_and_health(config: Config, tmp_path: Path) -> None:
    snapshot = engine.backup(config, tag="essai").snapshot
    server = api.start("127.0.0.1:0", config.repository, tmp_path / "state.json")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, body = _get(base, "/api/snapshots")
        assert code == 200
        assert body == [
            {
                "id": snapshot.id,
                "created_at": snapshot.created_at,
                "tag": "essai",
                "files": snapshot.file_count,
                "size": snapshot.total_size,
            }
        ]
        # Pas encore de fichier d'état : le service démarre, l'API le dit sans planter.
        code, body = _get(base, "/api/health")
        assert code == 503 and "aucun état" in body["error"]
        assert _get(base, "/api/inconnu")[0] == 404
    finally:
        server.shutdown()


def test_api_rejects_a_malformed_listen_address(tmp_path: Path) -> None:
    with pytest.raises(api.ConfigError):
        api.start("8000", tmp_path, tmp_path / "state.json")
