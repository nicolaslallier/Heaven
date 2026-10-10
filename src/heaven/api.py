"""API HTTP en lecture seule, lancée par `heaven serve --listen`.

Elle n'est jamais publiée telle quelle : nginx (web/nginx.conf) la relaie sous
/api/ et sert l'interface Vue à côté. D'où l'absence d'authentification ici —
le port n'est joignable que depuis le réseau de la pile.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import notify, scheduler, status
from .errors import ConfigError, HeavenError
from .repository import Repository

HEALTH_GRACE = timedelta(hours=1)


def start(listen: str, repository: Path, state_path: Path) -> ThreadingHTTPServer:
    """Démarre l'API dans un fil d'arrière-plan ; « hôte:port », ex. « 0.0.0.0:8000 »."""
    host, separator, port = listen.rpartition(":")
    if not separator or not port.isdigit():
        raise ConfigError(f"adresse d'écoute invalide : {listen!r} (attendu : hôte:port)")

    def history(state: dict[str, object]) -> list[dict[str, object]]:
        runs = state.get("history")
        return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []

    def health() -> dict[str, object]:
        healthy, reason = scheduler.health(state_path, grace=HEALTH_GRACE)
        state = scheduler.read_state(state_path)
        now = datetime.now()
        disk = status.disk_info(repository, history(state), now)
        verdict = status.evaluate(state, now, HEALTH_GRACE, disk)
        return {
            "healthy": healthy,
            "reason": reason,
            "status": verdict.status,
            "summary": verdict.reason,
            "disk": disk,
            "notifications": notifications(state),
            "last_verify": state.get("last_verify"),
            "state": state,
        }

    def notifications(state: dict[str, object]) -> dict[str, object]:
        config = notify.NotifyConfig.from_env()
        memory = state.get("notify")
        memory = memory if isinstance(memory, dict) else {}
        return {
            "email": notify.mask_address(config.email_to) if config.email_enabled else None,
            "push": config.push_enabled,
            "last_attempt": memory.get("last_attempt"),
            "last_result": memory.get("last_result"),
        }

    def runs(query: dict[str, str]) -> list[dict[str, object]]:
        try:
            limit = max(1, min(int(query.get("limit", "30")), scheduler.HISTORY_LIMIT))
        except ValueError:
            limit = 30
        result = []
        for run in history(scheduler.read_state(state_path))[:limit]:
            entry = dict(run)
            try:
                started = datetime.fromisoformat(str(run["started_at"]))
                finished = datetime.fromisoformat(str(run["finished_at"]))
                entry["duration_seconds"] = int((finished - started).total_seconds())
            except (KeyError, ValueError):
                entry["duration_seconds"] = None
            result.append(entry)
        return result

    def repository_info() -> dict[str, object]:
        state = scheduler.read_state(state_path)
        cached = state.get("repository")
        info: dict[str, object] = dict(cached) if isinstance(cached, dict) else {}
        info.setdefault("bytes", None)
        info["disk"] = status.disk_info(repository, history(state))
        return info

    def snapshots() -> list[dict[str, object]]:
        return [
            {
                "id": snapshot.id,
                "created_at": snapshot.created_at,
                "tag": snapshot.tag,
                "files": snapshot.file_count,
                "size": snapshot.total_size,
            }
            for snapshot in Repository.open(repository).list_snapshots()
        ]

    routes = {
        "/api/health": lambda _query: health(),
        "/api/snapshots": lambda _query: snapshots(),
        "/api/runs": runs,
        "/api/repository": lambda _query: repository_info(),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path, _, raw_query = self.path.partition("?")
            query = {k: v[0] for k, v in urllib.parse.parse_qs(raw_query).items()}
            route = routes.get(path)
            if route is None:
                self._send(404, {"error": "introuvable"})
                return
            try:
                self._send(200, route(query))
            except HeavenError as error:
                # Pas encore d'état ou de dépôt : le service démarre, ce n'est pas un bogue.
                self._send(503, {"error": str(error)})

        def _refuse(self) -> None:
            self._send(405, {"error": "API en lecture seule"}, allow="GET")

        do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

        def _send(self, code: int, payload: object, allow: str | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if allow:
                self.send_header("Allow", allow)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # nginx journalise déjà chaque requête ; les journaux restent ceux des cycles.

    server = ThreadingHTTPServer((host, int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
