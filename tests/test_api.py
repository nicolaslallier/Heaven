from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from heaven import api, engine, scheduler
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


def test_api_serves_history_repository_and_status(config: Config, tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    scheduler.serve(
        config, scheduler.parse_schedule("1s"), state_path=state, max_cycles=2, log=lambda _m: None
    )
    server = api.start("127.0.0.1:0", config.repository, state)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, body = _get(base, "/api/runs?limit=1")
        assert code == 200 and len(body) == 1
        assert body[0]["status"] == "ok" and body[0]["duration_seconds"] >= 0
        assert len(_get(base, "/api/runs")[1]) == 2

        code, body = _get(base, "/api/repository")
        assert code == 200 and body["snapshots"] == 2 and body["bytes"] > 0
        assert body["disk"]["total"] > 0

        code, body = _get(base, "/api/health")
        assert body["status"] in ("ok", "warning") and "summary" in body
        assert body["notifications"] == {
            "email": None, "push": False, "last_attempt": None, "last_result": None,
        }  # fmt: skip

        request = urllib.request.Request(base + "/api/runs", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request)
        assert refused.value.code == 405
    finally:
        server.shutdown()


def test_api_browses_a_snapshot_folder_by_folder(config: Config, tmp_path: Path) -> None:
    snapshot = engine.backup(config).snapshot
    server = api.start("127.0.0.1:0", config.repository, tmp_path / "state.json")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, root = _get(base, "/api/snapshots/latest/files")
        assert code == 200 and root["snapshot"] == snapshot.id
        assert [(e["name"], e["type"]) for e in root["entries"]] == [("data", "dir")]

        _, top = _get(base, "/api/snapshots/latest/files?path=data")
        assert [e["name"] for e in top["entries"]][0] == "docs"
        assert "copie-de-a.txt" in [e["name"] for e in top["entries"]]

        code, docs = _get(base, "/api/snapshots/latest/files?path=data/docs")
        assert [e["name"] for e in docs["entries"]] == ["a.txt", "b.txt"]
        assert docs["entries"][0]["size"] > 0 and len(docs["entries"][0]["mode"]) == 4

        code, page = _get(base, "/api/snapshots/latest/files?path=data/docs&limit=1&offset=1")
        assert page["total"] == 2 and [e["name"] for e in page["entries"]] == ["b.txt"]

        code, found = _get(base, "/api/snapshots/latest/files?q=B.TXT")
        assert [e["name"] for e in found["entries"]] == ["data/docs/b.txt"]

        assert _get(base, "/api/snapshots/inconnu/files")[0] == 404
    finally:
        server.shutdown()
