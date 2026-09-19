"""API HTTP en lecture seule, lancée par `heaven serve --listen`.

Elle n'est jamais publiée telle quelle : nginx (web/nginx.conf) la relaie sous
/api/ et sert l'interface Vue à côté. D'où l'absence d'authentification ici —
le port n'est joignable que depuis le réseau de la pile.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import scheduler
from .errors import ConfigError, HeavenError
from .repository import Repository

HEALTH_GRACE = timedelta(hours=1)


def start(listen: str, repository: Path, state_path: Path) -> ThreadingHTTPServer:
    """Démarre l'API dans un fil d'arrière-plan ; « hôte:port », ex. « 0.0.0.0:8000 »."""
    host, separator, port = listen.rpartition(":")
    if not separator or not port.isdigit():
        raise ConfigError(f"adresse d'écoute invalide : {listen!r} (attendu : hôte:port)")

    def health() -> dict[str, object]:
        healthy, reason = scheduler.health(state_path, grace=HEALTH_GRACE)
        return {"healthy": healthy, "reason": reason, "state": scheduler.read_state(state_path)}

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

    routes = {"/api/health": health, "/api/snapshots": snapshots}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = routes.get(self.path.split("?", 1)[0])
            if route is None:
                self._send(404, {"error": "introuvable"})
                return
            try:
                self._send(200, route())
            except HeavenError as error:
                # Pas encore d'état ou de dépôt : le service démarre, ce n'est pas un bogue.
                self._send(503, {"error": str(error)})

        def _send(self, code: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # nginx journalise déjà chaque requête ; les journaux restent ceux des cycles.

    server = ThreadingHTTPServer((host, int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
