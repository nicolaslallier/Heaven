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
| `heaven serve` | Mode appliance : sauvegarde en boucle selon une planification |
| `heaven health` | État du service planifié (sonde de santé du conteneur) |

Un instantané se désigne par son identifiant complet, un préfixe non ambigu,
ou `latest`. `verify` retourne le code 2 si le dépôt est corrompu, ce qui
permet de l'utiliser dans une tâche planifiée.

## En conteneur (Docker, Portainer)

`heaven serve` transforme la CLI en appliance : un conteneur qui reste allumé,
sauvegarde selon une planification, applique la rétention, vérifie
périodiquement le dépôt et renseigne la sonde de santé Docker.

```bash
docker build -t heaven-backup:latest .
docker run -d --name heaven --restart unless-stopped \
  -e HEAVEN_SOURCES=/sources -e HEAVEN_REPOSITORY=/repository \
  -e HEAVEN_SCHEDULE=02:30 -e HEAVEN_KEEP_LAST=7 -e TZ=Europe/Paris \
  -v /srv:/sources:ro -v heaven-repository:/repository \
  heaven-backup:latest
```

Ou, avec la pile fournie : `cp .env.example .env && docker compose up -d`.

En conteneur, `heaven.toml` devient facultatif : toute la configuration se lit
dans l'environnement (`HEAVEN_SOURCES`, `HEAVEN_REPOSITORY`, `HEAVEN_EXCLUDES`,
`HEAVEN_SCHEDULE`, `HEAVEN_KEEP_*`…), ce qui se saisit directement dans les
variables d'une pile Portainer. Un `heaven.toml` monté dans le conteneur reste
prioritaire (`HEAVEN_CONFIG` indique où le trouver).

| Fichier | Rôle |
| --- | --- |
| `Dockerfile` | L'image de l'appliance |
| `docker-compose.yml` | Pile construite depuis les sources (`docker compose up -d`, Portainer « Repository ») |
| `deploy/portainer/stack.yml` | Pile à base d'image publiée, pour l'éditeur web de Portainer |
| `.env.example` | Les variables et leurs valeurs par défaut |

**[docs/portainer.md](docs/portainer.md)** détaille le déploiement pas à pas :
construction par Portainer ou image publiée, montage des sources, restauration
depuis un conteneur jetable, droits d'accès.

### Planification

`HEAVEN_SCHEDULE` (ou `--schedule`) accepte une heure fixe `02:30`, plusieurs
heures `02:30,14:00`, ou un intervalle `6h` / `90m`. Les heures sont
interprétées dans le fuseau du conteneur (`TZ`).

Chaque cycle enchaîne sauvegarde, rétention, puis — tous les
`HEAVEN_VERIFY_EVERY` cycles — une vérification complète du dépôt. Deux
garde-fous protègent les données existantes : un cycle dont **aucune** source
n'est accessible (volume oublié ou démonté) n'écrit pas d'instantané, et un
instantané vide ne déclenche jamais la rétention.

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

Le `Makefile` crée l'environnement virtuel au premier appel ; `make help`
liste toutes les cibles.

```bash
make install    # venv + installation éditable avec les dépendances de dev
make test       # suite de tests
make lint       # lint
make format     # formatage
make check      # ce que vérifie la CI : lint + formatage + tests
```

### Organisation du code

| Module | Responsabilité |
| --- | --- |
| `heaven/cli.py` | Analyse des arguments et affichage |
| `heaven/config.py` | Lecture et validation de `heaven.toml` et de l'environnement |
| `heaven/scanner.py` | Parcours des sources, exclusions, métadonnées |
| `heaven/repository.py` | Magasin d'objets et instantanés sur disque |
| `heaven/engine.py` | `backup`, `restore`, `verify`, `prune` |
| `heaven/manifest.py` | Sérialisation des instantanés |
| `heaven/hashing.py` | Empreintes calculées en flux |
| `heaven/scheduler.py` | Mode appliance : planification, état, sonde de santé |

## Limites connues

- Dépôt local uniquement : pas encore de destination distante (SFTP, S3).
- Pas de chiffrement des objets.
- La déduplication se fait au niveau du fichier, pas du bloc : modifier un octet
  d'un gros fichier en réécrit le contenu entier.
- Les propriétaires (uid/gid) ne sont pas restaurés, seuls les droits d'accès.
