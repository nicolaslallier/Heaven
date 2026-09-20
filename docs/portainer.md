# Heaven en conteneur, déployé par Portainer

Heaven tourne en mode *appliance* : un conteneur qui reste allumé, sauvegarde
selon une planification, applique la rétention, vérifie régulièrement le dépôt,
et expose son état à la sonde de santé Docker que Portainer affiche.

La pile porte deux services :

| Service | Rôle | Port publié |
| --- | --- | --- |
| `heaven` | l'appliance : planification, sauvegardes, rétention, vérification ; API en lecture seule sur `:8000`, interne à la pile | aucun |
| `heaven-web` | nginx : l'interface de consultation, et `/api/` relayé vers `heaven` | aucun — joint `infra-net`, exposé par le nginx d'Infra |

La pile déployée (`deploy/portainer/stack.yml`) ne publie donc aucun port : elle
rejoint le réseau `infra-net` de la pile
[Infra](https://github.com/nicolaslallier/Infra), dont le nginx la sert sur
<https://heaven.infra.famillelallier.net> (`nginx/conf.d/heaven.conf` là-bas).
Le service s'y nomme `heaven-web` et non `web`, parce que Compose enregistre le
nom du service comme alias DNS sur chaque réseau rejoint et qu'`infra-net` est
partagé par plusieurs piles.

`docker-compose.yml`, la pile construite depuis les sources, reste celle du
poste de développement : elle publie `HEAVEN_WEB_PORT`, sur la boucle locale
par défaut (`HEAVEN_WEB_BIND`).

Tout se règle par variables d'environnement — aucun `heaven.toml` n'est requis.

## 1. Choisir la voie de déploiement

| Voie | Quand | Fichier |
| --- | --- | --- |
| **GitHub Actions** | la pile doit se remettre à jour toute seule | `deploy/portainer/stack.yml` |
| **Repository** | Portainer construit l'image depuis GitHub | `docker-compose.yml` |
| **Web editor** | une image est déjà publiée (GHCR, registre privé) | `deploy/portainer/stack.yml` |

La voie **GitHub Actions** est celle qui tient dans la durée : elle installe la
même pile que le « Web editor », mais en la déclarant à Portainer une fois pour
toutes, puis en la redéployant à chaque image publiée. Section 6.

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
| `HEAVEN_WEB_PORT` | `8080` | port **hôte** de l'interface web — `docker-compose.yml` seulement, la pile déployée ne publie rien |
| `HEAVEN_WEB_BIND` | `127.0.0.1` | interface hôte sur laquelle ce port est publié (`0.0.0.0` pour l'ouvrir au LAN) |
| `HEAVEN_IMAGE` | `ghcr.io/nicolaslallier/heaven:latest` | image de l'appliance |
| `HEAVEN_WEB_IMAGE` | `ghcr.io/nicolaslallier/heaven-web:latest` | image de l'interface web |

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

Heaven refuse d'écrire un dépôt dans un répertoire qui contient déjà autre
chose — c'est ce qui l'empêche de se déverser au milieu de vos données. Deux
conséquences pour `HEAVEN_REPOSITORY_PATH` :

- pointez-le sur un **sous-répertoire** du montage (`/mnt/disque/heaven`), pas
  sur la racine : un disque fraîchement formaté contient déjà `lost+found`, et
  le dépôt serait refusé ;
- ne rangez pas le fichier d'état dedans : il a son propre volume
  (`/var/lib/heaven`), et c'est le défaut de l'image.

Le message `le répertoire existe et n'est pas un dépôt Heaven` dans les
journaux désigne exactement ce cas.

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

### L'interface web

Elle répond sur <https://heaven.infra.famillelallier.net>, servie par le nginx
d'Infra ; la pile elle-même ne publie aucun port. Le conteneur `heaven-web` a sa
propre pastille de santé, qui interroge la page servie.

Sur un poste de développement (`make web`, `docker-compose.yml`), c'est
`http://localhost:8080` — l'hôte étant la machine Docker, pas Portainer.

L'interface affiche la santé de l'appliance, lue par `/api/health` ; `/api/`
expose aussi la liste des instantanés (`/api/snapshots`), en lecture seule.
Heaven n'a pas d'authentification propre : ces métadonnées sont lisibles par
quiconque atteint le vhost. C'est la raison pour laquelle la pile ne publie plus
de port hôte — et, si la seule présence sur le LAN ne suffit pas comme barrière,
le vhost d'Infra accepte la grille oauth2-proxy/Keycloak des autres applications
(`nginx/conf.d/obsidian.conf` en est la recette).

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

## 6. Déployer depuis GitHub Actions

Les deux voies précédentes sont des gestes manuels : chaque nouvelle image
attend que quelqu'un ouvre Portainer et clique. Cette voie-ci ferme la boucle —
un push sur `main` construit l'image, la publie sur GHCR, puis redéploie la
pile — et c'est la même mécanique que le dépôt [Infra](https://github.com/nicolaslallier/Infra).

```
push sur main → « Image Docker » (runner GitHub) → ghcr.io/…/heaven:latest
                                                   ghcr.io/…/heaven-web:latest
                                                          ↓
              « Déploiement » (runner auto-hébergé) → API Portainer → pile « heaven »
```

Les deux images sont construites par le **même** workflow, en deux jobs : le
déploiement attend ce workflow, et publier l'interface ailleurs redéploierait la
pile avant que son image existe.

Le déclencheur est la **publication de l'image**, pas le push : la pile tire
`:latest`, et redéployer avant que le nouveau tag soit poussé relancerait
l'ancienne image en annonçant un succès. `.github/workflows/deploy.yml` attend
donc la fin de `.github/workflows/docker.yml` (`workflow_run`), et ne déploie
qu'une réussite sur `main`.

### Pourquoi un runner auto-hébergé

**Un runner hébergé par GitHub ne peut pas déployer cette pile.** Portainer
publie son API sur le LAN (`${LAN_IP}:9443`) et sur son réseau Docker, sans
aucune ingress publique vers l'un ni l'autre. Le runner doit être sur ce LAN, et
`docker-compose.runner.yml` l'y installe — dans son **propre projet compose**,
à côté de la pile qu'il déploie et non dedans : un redéploiement recrée tous les
conteneurs de la pile, et un runner recréé en plein job est un job qui ne rend
jamais son résultat.

### Mise en place, une fois

1. **Une clé d'API Portainer** : *My account* → *Access tokens*. Elle vaut root
   sur le démon Docker — la poser en **secret** du dépôt, jamais en variable :
   *Settings* → *Secrets and variables* → *Actions* → *Secrets* →
   `PORTAINER_API_KEY`.

2. **Les variables de la pile**, si les valeurs par défaut ne conviennent pas :
   *Variables* → `HEAVEN_STACK_ENV`, en lignes `KEY=VALUE` au format de
   `.env.example`. En pratique, ce sont les chemins hôtes :

   ```
   HEAVEN_SOURCES_PATH=/mnt/donnees
   HEAVEN_REPOSITORY_PATH=/mnt/nas/heaven
   HEAVEN_SCHEDULE=02:30
   TZ=Europe/Paris
   ```

   La variable est facultative : `stack.yml` porte une valeur par défaut pour
   chaque clé, et une pile déployée sans rien sauvegarde `/srv` à 02:30.
   `PORTAINER_*` y est filtré au passage — ces réglages pilotent le
   déploiement et n'ont rien à faire dans l'environnement d'un conteneur.

   Laissée vide sur une pile déjà installée, elle ne **remplace rien** : les
   variables déjà posées dans Portainer sont reprises telles quelles. Un
   redéploiement ne peut donc pas ramener la pile sur `/srv` en silence parce
   que la variable n'a pas été renseignée. Corollaire : pour changer une
   valeur, la changer dans `HEAVEN_STACK_ENV` — qui devient alors la liste
   complète — et non dans l'UI de Portainer, que le déploiement suivant
   écraserait.

3. **Le runner**, sur la machine qui porte Docker :

   ```bash
   echo 'GH_RUNNER_TOKEN=<PAT autorisé à enregistrer des runners>' > .runner.env
   mkdir -p /srv/heaven-runner/_work
   make runner-up
   make runner-logs          # jusqu'à « Listening for Jobs »
   ```

   `HEAVEN_RUNNER_WORKDIR` (défaut `/srv/heaven-runner/_work`) doit être un
   chemin **hôte**, monté au même chemin dans le conteneur. `ci-deploy.sh` y
   monte le checkout dans un conteneur jetable, et un montage imbriqué est
   résolu par le démon : depuis un volume nommé, le démon ne trouverait rien et
   monterait un répertoire vide — sans rien dire. Le script refuse alors de
   continuer et nomme ce cas.

Le premier déploiement **crée** la pile dans Portainer (méthode *Repository*,
sur `deploy/portainer/stack.yml`) ; les suivants la redéploient. Une pile
`heaven` déjà installée à la main par le « Web editor » n'est pas reprise :
la supprimer d'abord — les volumes, dépôt de sauvegarde compris, survivent à
la suppression d'une pile.

### À la main, sur la même API

```bash
make deploy            # redéploie main en retirant l'image à nouveau
make deploy-norepull   # redéploie sans retirer l'image (rare : l'image est la livraison)
make deploy-down       # arrête la pile — les volumes restent
make deploy-delete     # retire la pile de Portainer — les volumes restent
make deploy-selftest   # contrôle le script sans rien déployer
```

Ces cibles lisent `PORTAINER_API_KEY` dans `.portainer.env` (ignoré par git,
comme `.runner.env`, et pour la même raison : `.env` est remis tel quel à
Portainer comme environnement de la pile). Elles déploient **toujours GitHub
`main`**, jamais le checkout local : ce qui est déployé vient entièrement de
sources publiées — le compose depuis GitHub, l'image depuis GHCR — et aucun
fichier du dépôt n'est monté dans le conteneur.

### Note de sécurité, à lire avant de toucher à tout ceci

Ce dépôt est public et le runner détient `/var/run/docker.sock` : root sur le
démon Docker, le même pouvoir que l'UI de Portainer. Deux conséquences.

- **Ne jamais ajouter de déclencheur `pull_request` ou `pull_request_target` à
  un workflow qui tourne sur le label `heaven`.** La pull request d'un fork
  apporte son propre fichier de workflow : la combinaison donne l'exécution de
  code arbitraire, en root, sur la machine de déploiement. `deploy.yml` se
  déclenche sur `workflow_run` et `workflow_dispatch`, rien d'autre. Régler
  *Settings* → *Actions* → *General* → « Fork pull request workflows from
  outside collaborators » sur **Require approval for all outside
  collaborators** ; le défaut ne filtre que les premières contributions.
- **Fusionner sur `main` suffit désormais à exécuter du code sur la machine.**
  C'était déjà vrai de qui lançait `make deploy` ; c'est maintenant vrai de qui
  peut pousser sur `main`. La protection de branche est ce qui garde ces deux
  ensembles de la même taille.

## 7. Construire l'image à la main

```bash
docker build -t heaven-backup:latest .
docker run --rm \
  -e HEAVEN_SOURCES=/sources -e HEAVEN_REPOSITORY=/repository \
  -e HEAVEN_SCHEDULE=6h \
  -v /srv:/sources:ro -v heaven-repository:/repository \
  heaven-backup:latest
```

`make docker-build` et `make docker-up` enveloppent ces commandes.
