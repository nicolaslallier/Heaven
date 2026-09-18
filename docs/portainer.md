# Heaven en conteneur, déployé par Portainer

Heaven tourne en mode *appliance* : un conteneur qui reste allumé, sauvegarde
selon une planification, applique la rétention, vérifie régulièrement le dépôt,
et expose son état à la sonde de santé Docker que Portainer affiche.

Tout se règle par variables d'environnement — aucun `heaven.toml` n'est requis.

## 1. Choisir la voie de déploiement

| Voie | Quand | Fichier |
| --- | --- | --- |
| **Repository** | Portainer construit l'image depuis GitHub | `docker-compose.yml` |
| **Web editor** | une image est déjà publiée (GHCR, registre privé) | `deploy/portainer/stack.yml` |

### Voie « Repository » (construction par Portainer)

1. *Stacks* → *Add stack* → nom : `heaven`.
2. *Build method* : **Repository**.
3. *Repository URL* : `https://github.com/nicolaslallier/heaven`, *Reference* : `refs/heads/main`.
4. *Compose path* : `docker-compose.yml`.
5. Renseigner les variables (section suivante) dans *Environment variables*.
6. *Deploy the stack*.

### Voie « Web editor » (image publiée)

1. *Stacks* → *Add stack* → nom : `heaven` → **Web editor**.
2. Coller le contenu de `deploy/portainer/stack.yml`.
3. Renseigner les variables dans *Environment variables*.
4. *Deploy the stack*.

## 2. Les variables

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `HEAVEN_SOURCES_PATH` | `/srv` | répertoire **hôte** monté en lecture seule sur `/sources` |
| `HEAVEN_SOURCES` | `/sources` | sources vues du conteneur (séparées par `:` ou `,`) |
| `HEAVEN_REPOSITORY_PATH` | `heaven-repository` | volume nommé, ou chemin hôte (`/mnt/nas/heaven`) |
| `HEAVEN_REPOSITORY` | `/repository` | dépôt vu du conteneur |
| `HEAVEN_EXCLUDES` | `*.tmp,__pycache__,node_modules,.venv` | motifs exclus |
| `HEAVEN_SCHEDULE` | `02:30` | `02:30`, `02:30,14:00` ou un intervalle `6h` / `90m` |
| `HEAVEN_TAG` | `portainer` | étiquette portée par les instantanés |
| `HEAVEN_VERIFY_EVERY` | `7` | vérification d'intégrité tous les N cycles (`0` : jamais) |
| `HEAVEN_KEEP_LAST` / `_DAILY` / `_WEEKLY` / `_MONTHLY` | `7` / `7` / `4` / `6` | rétention |
| `HEAVEN_NO_INITIAL_BACKUP` | *(vide)* | `true` : attendre la première échéance au lieu de sauvegarder au démarrage |
| `TZ` | `Europe/Paris` | fuseau dans lequel `HEAVEN_SCHEDULE` est interprété |

Les chemins de `HEAVEN_SOURCES` sont ceux **du conteneur**. Pour sauvegarder
plusieurs répertoires hôtes, ajouter les montages correspondants et les lister :

```yaml
    volumes:
      - /home/nicolas/documents:/sources/documents:ro
      - /var/lib/docker/volumes:/sources/volumes:ro
    environment:
      HEAVEN_SOURCES: /sources/documents:/sources/volumes
```

> Le dépôt doit vivre **ailleurs** que les sources : un disque distinct, un
> montage NAS. Un dépôt posé au milieu des données sauvegardées disparaîtrait
> avec elles.

## 3. Vérifier que ça tourne

Les journaux (*Containers* → `heaven` → *Logs*) montrent chaque cycle :

```
2026-05-01T02:30:00  sauvegarde de /sources
2026-05-01T02:31:12  instantané 20260501T023000.123Z : 8421 fichiers, 37 nouveaux (18 MiB écrits), 8384 réutilisés
2026-05-01T02:31:13  rétention : 14 instantané(s) conservé(s), 1 supprimé(s), 4 MiB libérés
2026-05-01T02:31:20  prochaine sauvegarde à 2026-05-02T02:30:00
```

La pastille de santé du conteneur passe au rouge si le dernier cycle a échoué,
si le dépôt est corrompu, ou si l'échéance est dépassée de plus d'une heure.

## 4. Opérations courantes

Depuis *Containers* → `heaven` → *Console*, ou en ligne de commande :

```bash
docker exec heaven heaven snapshots            # lister les instantanés
docker exec heaven heaven list latest          # contenu du dernier instantané
docker exec heaven heaven verify               # contrôle d'intégrité complet
docker exec heaven heaven health               # état du service planifié
docker exec heaven heaven backup --tag manuel  # sauvegarde immédiate hors planification
```

### Restaurer

La restauration écrit des fichiers : elle a besoin d'une destination accessible
en écriture, que `/sources` (monté en lecture seule) n'est pas. Un conteneur
jetable, monté sur la même destination et le même dépôt, fait l'affaire :

```bash
docker run --rm \
  -v heaven-repository:/repository:ro \
  -v /srv/restauration:/restauration \
  ghcr.io/nicolaslallier/heaven:latest \
  restore latest --repository /repository --target /restauration
```

Ajouter `--path documents/rapport.odt` pour ne sortir qu'un fichier, et
`--overwrite` pour écraser ce qui existe déjà à la destination.

## 5. Droits d'accès

Le conteneur tourne en `root` : c'est ce qui lui permet de lire des sources
appartenant à n'importe quel utilisateur. Les fichiers qu'il ne peut pas lire
sont ignorés un par un et signalés dans les journaux (`attention : ignoré …`),
la sauvegarde continue.

Si vos sources sont lisibles par un utilisateur donné, restreindre le conteneur
est préférable :

```yaml
    user: "1000:1000"
```

Il faut alors que le dépôt et `/var/lib/heaven` soient écrivables par cet
utilisateur.

## 6. Construire l'image à la main

```bash
docker build -t heaven-backup:latest .
docker run --rm \
  -e HEAVEN_SOURCES=/sources -e HEAVEN_REPOSITORY=/repository \
  -e HEAVEN_SCHEDULE=6h \
  -v /srv:/sources:ro -v heaven-repository:/repository \
  heaven-backup:latest
```

`make docker-build` et `make docker-up` enveloppent ces commandes.
