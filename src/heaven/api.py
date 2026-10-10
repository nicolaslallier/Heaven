"""API HTTP en lecture seule, lancée par `heaven serve --listen`.

Elle n'est jamais publiée telle quelle : nginx (web/nginx.conf) la relaie sous
/api/ et sert l'interface Vue à côté. D'où l'absence d'authentification ici —
le port n'est joignable que depuis le réseau de la pile.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import notify, scheduler, status
from .errors import ConfigError, HeavenError, SnapshotNotFoundError
from .manifest import Snapshot
from .repository import Repository
from .scanner import FileEntry

HEALTH_GRACE = timedelta(hours=1)
MAX_CONCURRENT = 16  # requêtes simultanées ; au-delà, 503 plutôt qu'un fil de plus
DEFAULT_PAGE = 200
MAX_PAGE = 1000


def browse(snapshot: Snapshot, query: dict[str, str]) -> dict[str, object]:
    """Un dossier d'un instantané (ou une recherche par nom), paginé, métadonnées seulement."""
    path = query.get("path", "").strip("/")
    try:
        limit = max(1, min(int(query.get("limit", DEFAULT_PAGE)), MAX_PAGE))
        offset = max(0, int(query.get("offset", 0)))
    except ValueError:
        limit, offset = DEFAULT_PAGE, 0
    needle = query.get("q", "").lower()
    prefix = path + "/" if path else ""

    folders: dict[str, dict[str, object]] = {}
    files: list[dict[str, object]] = []
    for entry in snapshot.entries:
        if not entry.path.startswith(prefix):
            continue
        rest = entry.path[len(prefix) :]
        if not rest:
            continue
        if needle:
            if needle in rest.rsplit("/", 1)[-1].lower() and entry.kind != "dir":
                files.append(_describe(entry, rest))
            continue
        head, nested, _ = rest.partition("/")
        if nested or entry.kind == "dir":  # les dossiers implicites comptent aussi
            folders.setdefault(head, {"name": head, "type": "dir"})
        else:
            files.append(_describe(entry, head))

    ordered = sorted(folders.values(), key=lambda item: str(item["name"]).lower())
    ordered += sorted(files, key=lambda item: str(item["name"]).lower())
    return {
        "path": path,
        "total": len(ordered),
        "entries": ordered[offset : offset + limit],
    }


def _describe(entry: FileEntry, name: str) -> dict[str, object]:
    try:
        mtime: str | None = datetime.fromtimestamp(entry.mtime).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):  # date absurde dans un manifeste
        mtime = None
    return {
        "name": name,
        "type": entry.kind,
        "size": entry.size,
        "mtime": mtime,
        "mode": format(entry.mode & 0o7777, "04o"),
    }


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

    cache: dict[str, tuple[float, Snapshot]] = {}
    cache_lock = threading.Lock()

    def snapshot_files(snapshot_ref: str, query: dict[str, str]) -> dict[str, object]:
        repo = Repository.open(repository)
        resolved = repo.resolve_snapshot_id(snapshot_ref)
        # Un manifeste de 500 000 fichiers pèse lourd à relire : on garde le dernier.
        try:
            stamp = repo.snapshot_path(resolved).stat().st_mtime
        except OSError as error:  # supprimé par la rétention entre-temps
            raise SnapshotNotFoundError(f"instantané introuvable : {snapshot_ref}") from error
        with cache_lock:
            cached = cache.get(resolved)
            if cached is None or cached[0] != stamp:
                cached = (stamp, repo.load_snapshot(resolved))
                cache.clear()
                cache[resolved] = cached
            loaded = cached[1]
        return {"snapshot": resolved, **browse(loaded, query)}

    routes = {
        "/api/health": lambda _query: health(),
        "/api/snapshots": lambda _query: snapshots(),
        "/api/runs": runs,
        "/api/repository": lambda _query: repository_info(),
    }

    slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not slots.acquire(blocking=False):
                self._send(503, {"error": "serveur occupé, réessayez"})
                return
            try:
                self._get()
            except Exception as error:  # noqa: BLE001 - jamais de fil qui meurt en silence
                print(f"api : erreur inattendue sur {self.path} : {error!r}", file=sys.stderr)
                try:
                    self._send(500, {"error": "erreur interne"})
                except OSError:
                    pass  # client parti
            finally:
                slots.release()

        def _get(self) -> None:
            path, _, raw_query = self.path.partition("?")
            query = {k: v[0] for k, v in urllib.parse.parse_qs(raw_query).items()}
            route = routes.get(path)
            parts = path.split("/")
            if (
                route is None
                and len(parts) == 5
                and parts[:3] == ["", "api", "snapshots"]
                and parts[4] == "files"
            ):
                snapshot_ref = urllib.parse.unquote(parts[3])
                route = lambda q: snapshot_files(snapshot_ref, q)  # noqa: E731
            if route is None:
                self._send(404, {"error": "introuvable"})
                return
            try:
                self._send(200, route(query))
            except SnapshotNotFoundError as error:
                self._send(404, {"error": str(error)})
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
