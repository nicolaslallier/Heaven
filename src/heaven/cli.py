"""Interface en ligne de commande : `heaven`."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from . import __version__, api, engine, scheduler
from .config import ENV_PREFIX, Config, RetentionPolicy, config_from_env, find_config, load_config
from .errors import HeavenError
from .repository import Repository

EXAMPLE_CONFIG = """# Configuration Heaven
[backup]
sources = ["~/Documents", "~/Projets"]
repository = "~/sauvegardes/heaven"
excludes = ["*.tmp", "__pycache__", ".venv", "node_modules"]
follow_symlinks = false
compress = true

[retention]
keep_last = 7
keep_daily = 7
keep_weekly = 4
keep_monthly = 6
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heaven",
        description="Sauvegarde incrémentale de fichiers vers un dépôt local.",
    )
    parser.add_argument("--version", action="version", version=f"heaven {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="fichier de configuration (par défaut : heaven.toml trouvé en remontant)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="détailler chaque fichier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="créer une configuration et un dépôt")
    init.add_argument("--repository", type=Path, help="chemin du dépôt à créer")
    init.add_argument(
        "--config-path",
        type=Path,
        default=Path("heaven.toml"),
        help="où écrire l'exemple de configuration (par défaut : ./heaven.toml)",
    )

    backup = subparsers.add_parser("backup", help="créer un instantané")
    backup.add_argument("--tag", help="étiquette libre associée à l'instantané")
    backup.add_argument(
        "--source",
        type=Path,
        action="append",
        dest="sources",
        help="source à sauvegarder (répétable ; ignore la configuration)",
    )
    backup.add_argument("--repository", type=Path, help="dépôt de destination")
    backup.add_argument(
        "--exclude", action="append", dest="excludes", default=[], help="motif à exclure"
    )
    backup.add_argument(
        "--no-compress", action="store_true", help="stocker les contenus sans compression"
    )

    snapshots = subparsers.add_parser("snapshots", help="lister les instantanés")
    snapshots.add_argument("--repository", type=Path, help="dépôt à interroger")

    listing = subparsers.add_parser("list", help="lister le contenu d'un instantané")
    listing.add_argument("snapshot", nargs="?", default="latest", help="identifiant ou « latest »")
    listing.add_argument("--repository", type=Path, help="dépôt à interroger")

    restore = subparsers.add_parser("restore", help="restaurer un instantané")
    restore.add_argument("snapshot", nargs="?", default="latest", help="identifiant ou « latest »")
    restore.add_argument("--target", type=Path, required=True, help="répertoire de destination")
    restore.add_argument(
        "--path", action="append", dest="paths", default=[], help="fichier ou dossier à restaurer"
    )
    restore.add_argument(
        "--overwrite", action="store_true", help="écraser les fichiers déjà présents"
    )
    restore.add_argument("--repository", type=Path, help="dépôt source")

    verify = subparsers.add_parser("verify", help="vérifier l'intégrité du dépôt")
    verify.add_argument("snapshot", nargs="?", help="n'en vérifier qu'un (par défaut : tous)")
    verify.add_argument("--repository", type=Path, help="dépôt à vérifier")

    prune = subparsers.add_parser("prune", help="appliquer la politique de rétention")
    prune.add_argument("--repository", type=Path, help="dépôt à nettoyer")
    prune.add_argument("--keep-last", type=int, help="nombre d'instantanés récents à garder")
    prune.add_argument("--keep-daily", type=int, help="nombre de jours à garder")
    prune.add_argument("--keep-weekly", type=int, help="nombre de semaines à garder")
    prune.add_argument("--keep-monthly", type=int, help="nombre de mois à garder")
    prune.add_argument(
        "--dry-run", action="store_true", help="montrer ce qui serait supprimé, sans rien supprimer"
    )

    serve = subparsers.add_parser(
        "serve", help="mode appliance : sauvegarder en boucle selon une planification"
    )
    serve.add_argument(
        "--schedule",
        default=_env(f"{ENV_PREFIX}SCHEDULE", scheduler.DEFAULT_SCHEDULE),
        help="« 6h », « 90m », « 02:30 » ou « 02:30,14:00 » (défaut : %(default)s)",
    )
    serve.add_argument("--tag", default=_env(f"{ENV_PREFIX}TAG"), help="étiquette des instantanés")
    serve.add_argument(
        "--source",
        type=Path,
        action="append",
        dest="sources",
        help="source à sauvegarder (répétable ; ignore la configuration)",
    )
    serve.add_argument("--repository", type=Path, help="dépôt de destination")
    serve.add_argument(
        "--exclude", action="append", dest="excludes", default=[], help="motif à exclure"
    )
    serve.add_argument(
        "--no-initial-backup",
        action="store_true",
        default=_env_flag(f"{ENV_PREFIX}NO_INITIAL_BACKUP"),
        help="attendre la première échéance au lieu de sauvegarder au démarrage",
    )
    serve.add_argument(
        "--verify-every",
        type=int,
        default=_env_int(f"{ENV_PREFIX}VERIFY_EVERY", 7),
        help="vérifier l'intégrité tous les N cycles (0 : jamais ; défaut : %(default)s)",
    )
    serve.add_argument("--state-file", type=Path, help="fichier d'état relu par « heaven health »")
    serve.add_argument("--once", action="store_true", help="n'exécuter qu'un seul cycle")
    serve.add_argument(
        "--listen",
        default=_env(f"{ENV_PREFIX}LISTEN"),
        help="servir l'API en lecture seule sur « hôte:port » (derrière nginx)",
    )

    health = subparsers.add_parser(
        "health", help="état du service planifié (sonde de santé du conteneur)"
    )
    health.add_argument("--state-file", type=Path, help="fichier d'état écrit par « heaven serve »")
    health.add_argument(
        "--grace",
        type=int,
        default=_env_int(f"{ENV_PREFIX}HEALTH_GRACE", 3600),
        help="retard toléré, en secondes, sur l'échéance (défaut : %(default)s)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except HeavenError as error:
        print(f"erreur : {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrompu", file=sys.stderr)
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "init": _cmd_init,
        "backup": _cmd_backup,
        "snapshots": _cmd_snapshots,
        "list": _cmd_list,
        "restore": _cmd_restore,
        "verify": _cmd_verify,
        "prune": _cmd_prune,
        "serve": _cmd_serve,
        "health": _cmd_health,
    }
    return handlers[args.command](args)


# -- commandes -----------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    config_path: Path = args.config_path
    if config_path.exists():
        print(f"configuration déjà présente : {config_path}")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(f"configuration créée : {config_path}")
        print("modifiez « sources » et « repository », puis lancez : heaven backup")
    if args.repository:
        repository = Repository.initialize(args.repository.expanduser())
        print(f"dépôt prêt : {repository.path}")
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    verbose = args.verbose
    result = engine.backup(
        config,
        tag=args.tag,
        progress=(lambda path: print(f"  + {path}")) if verbose else None,
    )
    print(f"instantané {result.snapshot.id} créé dans {config.repository}")
    print(
        f"  {result.files_total} fichiers ({_human(result.bytes_scanned)}), "
        f"{result.files_stored} nouveaux ({_human(result.bytes_stored)} écrits), "
        f"{result.files_reused} réutilisés"
    )
    for path, reason in result.skipped:
        print(f"  ! ignoré {path} : {reason}", file=sys.stderr)
    return 0


def _cmd_snapshots(args: argparse.Namespace) -> int:
    repository_path = _resolve_repository(args)
    snapshots = Repository.open(repository_path).list_snapshots()
    if not snapshots:
        print("aucun instantané")
        return 0
    print(f"{'IDENTIFIANT':<26} {'FICHIERS':>9} {'TAILLE':>10}  ÉTIQUETTE")
    for snapshot in snapshots:
        print(
            f"{snapshot.id:<26} {snapshot.file_count:>9} "
            f"{_human(snapshot.total_size):>10}  {snapshot.tag or '-'}"
        )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    repository_path = _resolve_repository(args)
    snapshot = Repository.open(repository_path).load_snapshot(args.snapshot)
    print(f"instantané {snapshot.id} ({snapshot.created_at})")
    for entry in snapshot.entries:
        marker = {"dir": "d", "symlink": "l", "file": "-"}[entry.kind]
        size = _human(entry.size) if entry.is_file else ""
        print(f"{marker} {size:>10}  {entry.path}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    repository_path = _resolve_repository(args)
    result = engine.restore(
        repository_path,
        args.snapshot,
        args.target,
        paths=args.paths,
        overwrite=args.overwrite,
        progress=(lambda path: print(f"  > {path}")) if args.verbose else None,
    )
    print(
        f"restauré dans {args.target} : {result.files_restored} fichiers, "
        f"{result.dirs_created} dossiers, {result.symlinks_created} liens"
    )
    for path, reason in result.skipped:
        print(f"  ! ignoré {path} : {reason}", file=sys.stderr)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    repository_path = _resolve_repository(args)
    result = engine.verify(repository_path, args.snapshot)
    print(f"{result.snapshots_checked} instantané(s), {result.objects_checked} objet(s) vérifié(s)")
    for snapshot_id, path in result.missing:
        print(f"  MANQUANT  {snapshot_id}  {path}", file=sys.stderr)
    for snapshot_id, path in result.corrupted:
        print(f"  CORROMPU  {snapshot_id}  {path}", file=sys.stderr)
    if result.ok:
        print("dépôt intègre")
        return 0
    return 2


def _cmd_prune(args: argparse.Namespace) -> int:
    repository_path = _resolve_repository(args)
    policy = _resolve_policy(args)
    result = engine.prune(repository_path, policy, dry_run=args.dry_run)
    prefix = "à supprimer" if args.dry_run else "supprimé(s)"
    print(f"{len(result.kept)} instantané(s) conservé(s), {len(result.removed)} {prefix}")
    for snapshot_id in result.removed:
        print(f"  - {snapshot_id}")
    print(f"{result.objects_removed} objet(s), {_human(result.bytes_freed)} libéré(s)")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    schedule = scheduler.parse_schedule(args.schedule)
    state_path = args.state_file or scheduler.default_state_path()
    server = api.start(args.listen, config.repository, state_path) if args.listen else None
    try:
        return scheduler.serve(
            config,
            schedule,
            tag=args.tag,
            initial_backup=not args.no_initial_backup,
            verify_every=max(args.verify_every, 0),
            state_path=state_path,
            max_cycles=1 if args.once else None,
        )
    finally:
        if server is not None:
            server.shutdown()


def _cmd_health(args: argparse.Namespace) -> int:
    state_path = args.state_file or scheduler.default_state_path()
    healthy, reason = scheduler.health(state_path, grace=timedelta(seconds=max(args.grace, 0)))
    print(reason, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


# -- utilitaires ---------------------------------------------------------


def _resolve_config(args: argparse.Namespace) -> Config:
    """Construit la configuration : les options de la ligne de commande priment."""
    sources = getattr(args, "sources", None)
    repository = getattr(args, "repository", None)

    if sources:
        if repository is None:
            raise HeavenError("--repository est requis avec --source")
        return Config(
            sources=[source.expanduser() for source in sources],
            repository=repository.expanduser(),
            excludes=list(args.excludes),
            compress=not getattr(args, "no_compress", False),
            # Sans configuration, on ne devine pas de politique : tout est conservé.
            retention=RetentionPolicy(keep_last=0),
        )

    config = _load_config(args)
    if repository is not None:
        config.repository = repository.expanduser()
    if getattr(args, "excludes", None):
        config.excludes = [*config.excludes, *args.excludes]
    if getattr(args, "no_compress", False):
        config.compress = False
    return config


def _resolve_repository(args: argparse.Namespace) -> Path:
    if getattr(args, "repository", None):
        return args.repository.expanduser()
    configured = os.environ.get(f"{ENV_PREFIX}REPOSITORY")
    if configured and _config_path(args) is None:
        # Lister ou restaurer ne demande pas de sources : le dépôt suffit, ce qui
        # permet un conteneur jetable configuré du seul HEAVEN_REPOSITORY.
        return Path(configured).expanduser()
    return _load_config(args).repository


def _load_config(args: argparse.Namespace) -> Config:
    """Configuration : fichier demandé, sinon fichier trouvé, sinon environnement."""
    path = _config_path(args)
    if path is not None:
        return load_config(path)
    from_env = config_from_env()
    if from_env is None:
        raise HeavenError(
            "aucune configuration trouvée ; lancez « heaven init », passez "
            f"--config/--repository, ou définissez {ENV_PREFIX}SOURCES et {ENV_PREFIX}REPOSITORY"
        )
    return from_env


def _config_path(args: argparse.Namespace) -> Path | None:
    if args.config:
        return args.config
    configured = os.environ.get(f"{ENV_PREFIX}CONFIG")
    if configured:
        return Path(configured).expanduser()
    return find_config()


def _resolve_policy(args: argparse.Namespace) -> RetentionPolicy:
    overrides = {
        key: getattr(args, key)
        for key in ("keep_last", "keep_daily", "keep_weekly", "keep_monthly")
        if getattr(args, key) is not None
    }
    if overrides:
        return RetentionPolicy(**{"keep_last": 0, **overrides})
    if args.repository:
        # Sans configuration, on ne devine pas de politique : tout est conservé.
        return RetentionPolicy(keep_last=0)
    return _load_config(args).retention


def _env(name: str, default: str | None = None) -> str | None:
    """Valeur d'environnement, utilisée comme défaut des options en mode conteneur."""
    value = os.environ.get(name, "").strip()
    return value or default


def _env_flag(name: str) -> bool:
    return (os.environ.get(name, "").strip().lower()) in {"1", "true", "yes", "on", "oui"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise HeavenError(f"{name} doit être un entier (reçu : {value!r})") from exc


def _human(size: float) -> str:
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if size < 1024 or unit == "Tio":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Tio"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
