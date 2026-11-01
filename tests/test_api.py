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
        with error:
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
        server.server_close()


def test_api_rejects_a_malformed_listen_address(tmp_path: Path) -> None:
    with pytest.raises(api.ConfigError):
        api.start("8000", tmp_path, tmp_path / "state.json")


def test_api_serves_history_repository_and_status(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Indépendant de l'espace libre réel de la machine qui lance les tests.
    monkeypatch.setenv("HEAVEN_DISK_WARN", "0")
    monkeypatch.setenv("HEAVEN_DISK_CRIT", "0")
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

        assert _request(base, "/api/runs", "POST")[0] == 405
    finally:
        server.shutdown()
        server.server_close()


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
        server.server_close()


def test_describe_survives_an_absurd_mtime() -> None:
    entry = api.FileEntry(path="a", kind="file", size=1, mtime=1e30)
    assert api._describe(entry, "a")["mtime"] is None


def test_unexpected_error_gives_500_json(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("bogue")

    monkeypatch.setattr(api.Repository, "open", boom)
    server = api.start("127.0.0.1:0", config.repository, tmp_path / "state.json")
    try:
        code, body = _get(f"http://127.0.0.1:{server.server_address[1]}", "/api/snapshots")
        assert (code, body) == (500, {"error": "erreur interne"})
    finally:
        server.shutdown()
        server.server_close()


def test_saturated_server_answers_503(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAX_CONCURRENT", 0)
    server = api.start("127.0.0.1:0", config.repository, tmp_path / "state.json")
    try:
        code, _ = _get(f"http://127.0.0.1:{server.server_address[1]}", "/api/snapshots")
        assert code == 503
    finally:
        server.shutdown()
        server.server_close()


def test_public_state_hides_notification_errors() -> None:
    state = {"notify": {"last_result": "échec", "last_errors": ["refusé : n@example.net"]}}
    assert api.public_state(state)["notify"] == {"last_result": "échec"}
    assert "last_errors" in state["notify"]  # l'original reste intact


def _serve(config: Config, state: Path) -> tuple[api.ThreadingHTTPServer, str]:
    server = api.start("127.0.0.1:0", config.repository, state)
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _request(base: str, path: str, method: str) -> tuple[int, Any, bytes]:
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.headers, error.read()


def test_health_200_and_query_string_is_ignored(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEAVEN_DISK_WARN", "0")
    monkeypatch.setenv("HEAVEN_DISK_CRIT", "0")
    state = tmp_path / "state.json"
    scheduler.serve(
        config, scheduler.parse_schedule("1s"), state_path=state, max_cycles=1, log=lambda _m: None
    )
    server, base = _serve(config, state)
    try:
        code, body = _get(base, "/api/health")
        assert code == 200 and body["healthy"] is True
        assert _get(base, "/api/health?x=1&y=2")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_unreadable_manifest_gives_503_json(config: Config, tmp_path: Path) -> None:
    engine.backup(config)
    for manifest in (config.repository / "snapshots").glob("*.json"):
        manifest.write_text("{pas du json")
    server, base = _serve(config, tmp_path / "state.json")
    try:
        code, body = _get(base, "/api/snapshots")
        assert code == 503 and body["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_injected_oserror_gives_500_json(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> None:
        raise OSError("disque")

    monkeypatch.setattr(api.Repository, "open", boom)
    server, base = _serve(config, tmp_path / "state.json")
    try:
        assert _get(base, "/api/snapshots") == (500, {"error": "erreur interne"})
    finally:
        server.shutdown()
        server.server_close()


def test_head_has_no_body_and_other_methods_give_405(config: Config, tmp_path: Path) -> None:
    engine.backup(config)
    server, base = _serve(config, tmp_path / "state.json")
    try:
        code, headers, body = _request(base, "/api/snapshots", "HEAD")
        assert code == 200 and body == b"" and int(headers["Content-Length"]) > 0
        assert _request(base, "/api/inconnu", "HEAD")[0] == 404
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            code, headers, body = _request(base, "/api/snapshots", method)
            assert code == 405 and "GET" in headers["Allow"], method
            assert json.loads(body)["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_snapshot_listing_is_cached_until_the_directory_changes(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine.backup(config)
    server, base = _serve(config, tmp_path / "state.json")
    try:
        assert len(_get(base, "/api/snapshots")[1]) == 1
        original = api.Repository.load_snapshot
        calls: list[str] = []

        def spy(self: api.Repository, snapshot_id: str) -> api.Snapshot:
            calls.append(snapshot_id)
            return original(self, snapshot_id)

        monkeypatch.setattr(api.Repository, "load_snapshot", spy)
        assert len(_get(base, "/api/snapshots")[1]) == 1 and calls == []
        engine.backup(config)
        assert len(_get(base, "/api/snapshots")[1]) == 2
    finally:
        server.shutdown()
        server.server_close()


def test_listen_errors_are_heaven_errors(config: Config, tmp_path: Path) -> None:
    server, _ = _serve(config, tmp_path / "state.json")
    try:
        busy = f"127.0.0.1:{server.server_address[1]}"
        with pytest.raises(api.HeavenError):
            api.start(busy, config.repository, tmp_path / "state.json")
        with pytest.raises(api.HeavenError):
            api.start("hote.invalide.example:8000", config.repository, tmp_path / "state.json")
    finally:
        server.shutdown()
        server.server_close()


def test_bracketed_ipv6_is_accepted(config: Config, tmp_path: Path) -> None:
    try:
        server = api.start("[::1]:0", config.repository, tmp_path / "state.json")
    except api.HeavenError:
        pytest.skip("IPv6 indisponible")
    server.shutdown()
    server.server_close()
