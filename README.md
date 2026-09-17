# Heaven

Outil de sauvegarde incrémentale de fichiers, en Python, sans dépendance externe.

Les contenus sont stockés dans un dépôt **adressé par contenu** : chaque fichier est
identifié par son empreinte SHA-256 et n'est écrit qu'une seule fois. Deux fichiers
identiques — dans la même sauvegarde ou d'une sauvegarde à l'autre — ne coûtent qu'un
seul objet. Les sauvegardes successives sont donc incrémentales sans avoir à se fier
aux dates de modification, et chaque instantané reste restaurable indépendamment
des autres.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Démarrage rapide

```bash
heaven init --repository ~/sauvegardes/heaven   # crée heaven.toml et le dépôt
$EDITOR heaven.toml                              # renseigner sources et repository
heaven backup --tag quotidien                    # créer un instantané
heaven snapshots                                 # lister les instantanés
heaven restore latest --target /tmp/restauration # restaurer le plus récent
heaven verify                                    # contrôler l'intégrité du dépôt
heaven prune                                     # appliquer la rétention
```

Sans configuration, tout tient sur une ligne :

```bash
heaven backup --source ~/Documents --repository ~/sauvegardes/heaven --exclude '*.log'
```

## Configuration

`heaven.toml`, cherché dans le répertoire courant puis ses parents (ou passé
via `--config`). Les chemins relatifs sont résolus par rapport au fichier.

```toml
[backup]
sources = ["~/Documents", "~/Projets"]
repository = "~/sauvegardes/heaven"
excludes = ["*.tmp", "__pycache__", ".venv", "node_modules"]
follow_symlinks = false
compress = true

[retention]
keep_last = 7     # les 7 instantanés les plus récents
keep_daily = 7    # un par jour, sur 7 jours
keep_weekly = 4
keep_monthly = 6
```

Un motif d'exclusion sans `/` est comparé à chaque composant du chemin
(`*.log` exclut aussi `var/app.log`) ; un motif avec `/` est comparé au
chemin complet relatif à l'instantané (`data/build`).

Une section `[retention]` vide, ou absente, conserve **tout** : `prune` ne
supprime jamais d'instantané sans politique explicite.

## Commandes

| Commande | Rôle |
| --- | --- |
| `heaven init` | Écrit un `heaven.toml` d'exemple et initialise le dépôt |
| `heaven backup` | Crée un instantané des sources (`--tag`, `--source`, `--exclude`) |
| `heaven snapshots` | Liste les instantanés du dépôt |
| `heaven list [ID]` | Affiche le contenu d'un instantané |
| `heaven restore [ID]` | Restaure vers `--target` (`--path`, `--overwrite`) |
| `heaven verify [ID]` | Relit chaque objet et compare son empreinte |
| `heaven prune` | Applique la rétention puis supprime les objets orphelins |

Un instantané se désigne par son identifiant complet, un préfixe non ambigu,
ou `latest`. `verify` retourne le code 2 si le dépôt est corrompu, ce qui
permet de l'utiliser dans une tâche planifiée.

## Structure du dépôt

```
depot/
├── config.json                 format du dépôt et algorithme d'empreinte
├── objects/
│   └── ab/cdef…[.gz]           un contenu unique, gzip par défaut
└── snapshots/
    └── 20260917T185902.016Z.json
```

Un instantané est un simple fichier JSON : la liste des fichiers, dossiers et
liens symboliques avec leurs métadonnées (taille, mode, date) et l'empreinte du
contenu associé. Les écritures passent par un fichier temporaire suivi d'un
renommage atomique : une interruption ne laisse jamais de fichier à moitié écrit.

## Développement

```bash
pytest          # suite de tests
ruff check .    # lint
ruff format .   # formatage
```

### Organisation du code

| Module | Responsabilité |
| --- | --- |
| `heaven/cli.py` | Analyse des arguments et affichage |
| `heaven/config.py` | Lecture et validation de `heaven.toml` |
| `heaven/scanner.py` | Parcours des sources, exclusions, métadonnées |
| `heaven/repository.py` | Magasin d'objets et instantanés sur disque |
| `heaven/engine.py` | `backup`, `restore`, `verify`, `prune` |
| `heaven/manifest.py` | Sérialisation des instantanés |
| `heaven/hashing.py` | Empreintes calculées en flux |

## Limites connues

- Dépôt local uniquement : pas encore de destination distante (SFTP, S3).
- Pas de chiffrement des objets.
- La déduplication se fait au niveau du fichier, pas du bloc : modifier un octet
  d'un gros fichier en réécrit le contenu entier.
- Les propriétaires (uid/gid) ne sont pas restaurés, seuls les droits d'accès.
