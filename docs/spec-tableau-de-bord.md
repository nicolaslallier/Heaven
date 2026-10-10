# Spécification — Tableau de bord des sauvegardes

> Statut : **brouillon** · Périmètre : interface web (`web/`) et API (`src/heaven/api.py`)

## 1. Objectif

Pouvoir ouvrir une page et répondre, en moins de cinq secondes, à :
**« mes sauvegardes vont-elles bien ? »**

Aujourd'hui, la page (`web/src/App.vue`) affiche une seule phrase tirée de
`/api/health`. Il faut un vrai tableau de bord qui dit si tout va bien, depuis
quand, et quoi faire sinon.

### Hors périmètre (v1)

- Lancer, annuler ou restaurer une sauvegarde depuis l'interface : l'API reste
  **en lecture seule**.
- Parcourir ou télécharger le *contenu* des fichiers (on ne liste que les
  noms et métadonnées, voir §3.6).

## 2. Utilisateurs et cas d'usage

Un seul profil : l'administrateur du serveur (usage familial / homelab).

| # | Cas d'usage | Réponse attendue |
| --- | --- | --- |
| U1 | « Est-ce que tout va bien ? » | Un indicateur global vert / orange / rouge, lisible d'un coup d'œil |
| U2 | « Quand a eu lieu la dernière sauvegarde, et a-t-elle réussi ? » | Date, durée, statut, nombre de fichiers |
| U3 | « Quand est la prochaine ? » | Date et heure, compte à rebours |
| U4 | « Que s'est-il passé ces dernières semaines ? » | Historique des cycles, échecs mis en évidence |
| U5 | « Combien de place ça prend, et ça grossit ? » | Taille du dépôt, évolution dans le temps |
| U6 | « Le dépôt est-il intact ? » | Date et résultat de la dernière vérification |
| U7 | « Pourquoi ça a échoué ? » | Message d'erreur et avertissements du cycle |
| U8 | « Quels instantanés puis-je restaurer ? » | Liste des instantanés (id, date, tag, fichiers, taille) |
| U9 | « Qu'y a-t-il dans cet instantané ? » | Arborescence des fichiers, navigable |
| U10 | « Suis-je prévenu si ça casse ? » | Courriel et push téléphone en cas d'échec, de retard ou de manque d'espace |
| U11 | « Et mes autres machines / dépôts ? » | Une vue agrégée de plusieurs instances Heaven |
| U12 | « Qui peut voir ça ? » | Accès protégé par un mot de passe |

## 3. Écran principal

Une page unique, responsive (utilisable sur téléphone), rafraîchie
automatiquement toutes les 30 s.

```
┌───────────────────────────────────────────────────────────┐
│  Heaven                                    ● Tout va bien │
├───────────────────────────────────────────────────────────┤
│ Dernière sauvegarde   Prochaine         Dernière vérif.   │
│ ✔ il y a 3 h          dans 21 h         ✔ il y a 2 j      │
│ 12 480 fichiers       02:30             0 objet corrompu  │
├───────────────────────────────────────────────────────────┤
│ Dépôt : 48,2 Gio · 14 instantanés · 211 034 objets        │
│ [ courbe de la taille du dépôt sur 90 jours ]             │
├───────────────────────────────────────────────────────────┤
│ Historique des cycles                                     │
│  ✔ 10 oct. 02:30  8 min   +312 Mio   12 480 fichiers      │
│  ✔  9 oct. 02:30  7 min   +95 Mio    12 471 fichiers      │
│  ✘  8 oct. 02:30  0 min   source /sources inaccessible    │
├───────────────────────────────────────────────────────────┤
│ Instantanés                                               │
│  20261010T023001Z  portainer  12 480 fichiers  41,0 Gio   │
└───────────────────────────────────────────────────────────┘
```

### 3.1 Indicateur global

| État | Couleur | Condition |
| --- | --- | --- |
| **OK** | vert | Dernier cycle `ok`, et pas en retard |
| **Attention** | orange | Dernier cycle `ok` mais avec avertissements (fichiers ignorés) ; ou première sauvegarde pas encore faite (`pending`) ; ou espace disque libre sous le seuil d'alerte (§3.7) |
| **Problème** | rouge | Dernier cycle `error` ; ou sauvegarde en retard (règle de `scheduler.health`, délai de grâce 1 h) ; ou dépôt corrompu ; ou API / service injoignable ; ou espace disque sous le seuil critique (§3.7) |
| **Inconnu** | gris | L'API répond 503 (service en démarrage, aucun état) |

L'indicateur est toujours accompagné d'une **phrase** (« Dernière sauvegarde en
échec : source /sources inaccessible »), jamais de la couleur seule
(accessibilité).

### 3.2 Cartes de synthèse

- **Dernière sauvegarde** : statut, date relative + date exacte au survol,
  durée, nombre de fichiers, octets écrits, avertissements (compteur
  cliquable).
- **Prochaine sauvegarde** : date, compte à rebours, planification
  (`02:30`, `6h`…). En retard : affichée en rouge.
- **Dernière vérification** : date, objets contrôlés, manquants / corrompus.
  Si aucune vérification n'a eu lieu : « jamais vérifié » en orange.

### 3.3 Historique des cycles

Tableau des N derniers cycles (défaut 30), du plus récent au plus ancien :
statut, début, durée, instantané créé, fichiers, octets écrits, instantanés
supprimés par la rétention, vérification oui / non. Un clic sur une ligne en
échec déplie l'erreur complète et les avertissements.

### 3.4 Dépôt

Taille totale, nombre d'objets, nombre d'instantanés, courbe de la taille
dans le temps (un point par cycle).

### 3.5 Instantanés

Liste issue de `/api/snapshots` : identifiant, date, tag, fichiers, taille.
Tri par date décroissante. Un clic ouvre le détail (§3.6). La restauration
reste en CLI : on affiche la commande `heaven restore <id>` copiable.

### 3.6 Détail d'un instantané

Arborescence navigable (dossier par dossier, chargée à la demande) avec nom,
type, taille, date de modification et droits ; recherche par nom dans
l'instantané. Aucun contenu de fichier n'est servi, seulement les métadonnées
du manifeste. Équivalent de `heaven list <id>`.

### 3.7 Espace disque

La carte « Dépôt » affiche l'espace libre et total du volume qui porte le
dépôt, et la tendance (jours restants au rythme des 30 derniers jours).
Seuils par variables d'environnement :

| Variable | Défaut | Effet |
| --- | --- | --- |
| `HEAVEN_DISK_WARN` | `20` | % libre en dessous duquel l'état passe à l'orange |
| `HEAVEN_DISK_CRIT` | `10` | % libre en dessous duquel l'état passe au rouge |

### 3.8 Vue multi-instances

Chaque machine garde sa propre instance Heaven (son API, son dépôt). Le
tableau de bord peut agréger plusieurs instances : un onglet « Toutes » liste
une ligne par instance (nom, indicateur, dernière sauvegarde, espace libre),
un clic ouvre le tableau de bord complet de l'instance.

*Repoussé : une seule machine aujourd'hui (§10).* Cible, le jour où il y en
aura plusieurs : chacune avec sa propre instance `heaven serve`. Configuration côté service web : variable `HEAVEN_INSTANCES`, liste
`nom=url` (ex. `maison=http://heaven:8000,nas=https://nas.lan/heaven`). Par
défaut, une seule instance : celle de la pile (comportement actuel). Chaque
instance garde son propre jeton (§6.1). Le relais se fait côté nginx, jamais
depuis le navigateur vers une autre origine.

## 4. Données : ce qui existe et ce qui manque

| Besoin | Source actuelle | Manque |
| --- | --- | --- |
| Statut global, raison | `GET /api/health` (`healthy`, `reason`) | — |
| Dernier cycle, prochain cycle | `state.json` → `last_run`, `next_run` | — |
| Liste des instantanés | `GET /api/snapshots` | — |
| **Historique** des cycles | — | `state.json` ne garde que **le dernier** cycle |
| Durée d'un cycle | `started_at` / `finished_at` | calculable côté client |
| Résultat de la dernière vérification | `last_run.verified` (booléen) | Dernière vérif. **réussie** : le champ est écrasé par les cycles sans vérification ; objets contrôlés / manquants / corrompus non conservés |
| Taille et nombre d'objets du dépôt | — | À calculer |
| Évolution de la taille | — | Dépend de l'historique |
| Espace disque libre / total | — | `shutil.disk_usage` sur le dépôt |
| Contenu d'un instantané | manifeste JSON sur disque | Route d'API paginée |
| Notifications | — | Module d'envoi (§7) |
| Plusieurs instances | — | Configuration et relais nginx (§3.8) |

## 5. Évolutions de l'API (lecture seule)

Toutes les routes restent en `GET`, JSON UTF-8, sous `/api/`.

### 5.1 `GET /api/health` *(inchangé, enrichi)*

Ajouter `status` ∈ `ok | warning | error | unknown`, calculé côté serveur
selon le tableau du §3.1, pour que la règle ne soit écrite qu'à un seul endroit.
`healthy` et `reason` sont conservés (compatibilité avec la sonde Docker).

### 5.2 `GET /api/runs?limit=30` *(nouveau)*

Historique des cycles, du plus récent au plus ancien. Chaque élément reprend
`CycleReport.to_dict()` plus :

```json
{
  "started_at": "2026-10-10T02:30:00",
  "finished_at": "2026-10-10T02:38:12",
  "duration_seconds": 492,
  "status": "ok",
  "snapshot": "20261010T023001.000Z",
  "files": 12480,
  "bytes_stored": 327155712,
  "snapshots_removed": 1,
  "bytes_freed": 104857600,
  "verified": true,
  "verify": { "objects_checked": 211034, "missing": 0, "corrupted": 0 },
  "repository_bytes": 51754236928,
  "error": null,
  "warnings": []
}
```

Stockage : `state.json` passe en `format: 2` avec une liste `history` bornée
(les 200 derniers cycles) ; un `format: 1` existant est lu sans erreur
(historique vide). Écriture toujours atomique (fichier temporaire + renommage).

### 5.3 `GET /api/repository` *(nouveau)*

```json
{ "bytes": 51754236928, "objects": 211034, "snapshots": 14 }
```

Le calcul parcourt le dépôt : il est mis en cache (recalculé à la fin de
chaque cycle et stocké dans `state.json`), jamais à chaque requête.

Ajouter `disk`: `{ "free": ..., "total": ..., "free_percent": ..., "days_left": ... }`
(`days_left` absent tant que l'historique est trop court).

### 5.4 `GET /api/snapshots` *(inchangé)*

### 5.4 bis `GET /api/snapshots/{id}/files?path=&limit=&offset=` *(nouveau)*

Contenu d'un dossier de l'instantané (`path` vide = racine), trié dossiers
d'abord ; `id` accepte `latest` et les préfixes non ambigus comme la CLI.
Paginé (défaut 200 entrées) car un instantané peut compter des centaines de
milliers de fichiers. `?q=` filtre par nom.

```json
{ "path": "Documents", "total": 1240, "entries": [
  { "name": "factures", "type": "dir" },
  { "name": "cv.pdf", "type": "file", "size": 83211, "mtime": "2026-09-01T10:00:00", "mode": "0644" } ] }
```

### 5.5 Erreurs

- `503` + `{"error": "..."}` tant que le service n'a rien écrit : l'interface
  affiche l'état « Inconnu », pas une erreur.
- `404` pour toute autre route (comportement actuel).
- `401` sans jeton valide (§6.1).

## 6. Interface (Vue)

- Garder Vue 3 + Vite (déjà en place), **sans dépendance lourde** : le
  graphique est un SVG maison (quelques dizaines de lignes) plutôt qu'une
  bibliothèque.
- Composants : `StatusBanner`, `SummaryCards`, `RunHistory`,
  `RepositoryChart`, `SnapshotList`.
- Rafraîchissement : `fetch` toutes les 30 s, en pause quand l'onglet est
  masqué. En cas d'échec réseau : on **garde** les dernières données et on
  affiche « données du hh:mm — Heaven injoignable » (bandeau rouge).
- Dates : fuseau du navigateur, format `fr-CA`/`fr-FR`, date relative avec
  date exacte au survol.
- Thème clair / sombre selon `prefers-color-scheme`.
- Accessibilité : contraste AA, statut annoncé en texte, navigation clavier.

### 6.1 Authentification et comptes

Comptes distincts, un par personne : l'identité est déléguée à **Keycloak**
(realm `infra`), déjà utilisé par l'Infra pour pgAdmin, S3, Prefect, etc.
Heaven ne stocke aucun mot de passe.

- **Connexion** : le nginx d'Infra place `heaven.infra.famillelallier.net`
  derrière un `oauth2-proxy` dédié (client Keycloak `heaven`, même recette que
  `oauth2-proxy-prefect`). Une personne sans compte, ou hors du groupe Keycloak
  `heaven`, n'atteint jamais la page.
- **Identité côté Heaven** : le proxy transmet `X-Auth-Request-User` et
  `X-Auth-Request-Email`. `heaven-web` affiche le nom connecté et le journalise
  pour chaque accès. Heaven n'implémente ni formulaire de connexion ni
  jeton JWT : le projet reste **sans dépendance externe**.
- **Confiance dans les en-têtes** : tout conteneur de `infra-net` peut
  atteindre `heaven-web`. Le nginx d'Infra ajoute donc un en-tête secret
  (`X-Heaven-Proxy`, valeur `HEAVEN_PROXY_SECRET`) que `heaven-web` exige,
  sinon 403. Sans lui, un en-tête d'identité serait falsifiable.
- **Rôles** : deux groupes Keycloak, `heaven` (lecture) et `heaven-admin`
  (réservé : futures actions d'écriture ; inutilisé en v1, tout est en lecture
  seule).
- **Jeton de service** : `HEAVEN_API_TOKEN` (`Authorization: Bearer`), pour le
  relais multi-instances (§3.8), hors navigateur. Comparaison à temps constant.
- La sonde de santé Docker (`heaven health`) lit le fichier d'état et ne passe
  pas par HTTP.
- Secrets (`HEAVEN_PROXY_SECRET`, `HEAVEN_API_TOKEN`) : variables de la pile
  ou OpenBao (`infra/apps/heaven`), jamais dans le dépôt.

Dépendance : la création du client Keycloak, de l'`oauth2-proxy` et du vhost
se fait dans le dépôt **Infra**, pas ici (§9, étape 3).

## 7. Notifications

Le service planifié envoie un message quand l'état se dégrade, pas à chaque
cycle. Il s'appuie sur les deux services de notification **déjà fournis par
l'Infra** sur `infra-net` : aucun code SMTP ni push à héberger.

| Canal | Service Infra | Pour quoi |
| --- | --- | --- |
| Courriel | relais Postfix `smtp:25` (sans authentification, sans TLS interne, `From` forcé sur `famillelallier.net`) | tous les messages |
| Téléphone | serveur ntfy `http://ntfy` (jeton par application, sujet par catégorie) | états rouges et rétablissement |

> **À noter : pas de SMS.** L'Infra n'offre pas de passerelle SMS, mais un push
> ntfy (application Android / iOS, notification instantanée sur le téléphone).
> La spec utilise donc ntfy à la place du SMS demandé. Si un vrai SMS est
> indispensable, il faudrait ajouter un fournisseur côté Infra (§11).

### 7.1 Événements

| Événement | Quand | Courriel | Push |
| --- | --- | --- | --- |
| Échec de sauvegarde | Un cycle finit en `error` | oui | oui, priorité haute |
| Retard | Sauvegarde non démarrée au-delà du délai de grâce | oui | oui, priorité haute |
| Dépôt corrompu | La vérification trouve des objets manquants ou corrompus | oui | oui, priorité urgente |
| Espace disque critique | Sous `HEAVEN_DISK_CRIT` | oui | oui, priorité haute |
| Espace disque bas | Sous `HEAVEN_DISK_WARN` | oui | non |
| Avertissements | Cycle `ok` avec fichiers ignorés | oui (résumé) | non |
| Rétablissement | Retour à l'état OK après un problème | oui | oui, priorité basse |
| Résumé hebdomadaire | Chaque lundi matin, **activé par défaut** | oui | non |

Le résumé hebdomadaire prouve que la surveillance elle-même fonctionne : son
absence un lundi est un signal. Contenu : cycles de la semaine (réussis /
échoués), instantanés créés, taille du dépôt et variation, espace libre,
dernière vérification.

### 7.2 Règles

- **Anti-bruit** : un message par *changement d'état* (OK → problème, problème
  → OK), puis un rappel toutes les 24 h tant que le problème dure
  (`HEAVEN_NOTIFY_REPEAT`).
- **Un seul destinataire** : `HEAVEN_NOTIFY_EMAIL_TO` (une adresse).
- **Courriel** : module `smtplib` de la bibliothèque standard, `smtp:25`, sans
  TLS ni authentification (réseau `infra-net`). Le projet reste **sans
  dépendance externe**.
- **Push** : `POST` HTTP vers `NTFY_URL/NTFY_TOPIC` avec `Authorization: Bearer
  $NTFY_TOKEN`, via `urllib` (bibliothèque standard). Le jeton s'obtient côté
  Infra par `make ntfy-provision app=heaven` : écriture seule, limitée aux
  sujets `heaven-*`. Sujet par défaut : `heaven-alertes`.
- **Réseau** : le service `heaven` (aujourd'hui sur le réseau par défaut de la
  pile seulement) doit **rejoindre `infra-net`** pour joindre `smtp` et `ntfy`.
  Son API reste sans port publié.
- Un échec d'envoi ne doit jamais arrêter le service ni bloquer un cycle : il
  est journalisé et réessayé au cycle suivant (contrat « au mieux » de l'Infra).
- L'état des notifications (dernier état notifié, date, derniers échecs
  d'envoi) vit dans `state.json` pour survivre aux redémarrages.
- `heaven notify-test` envoie un message d'essai sur chaque canal configuré.
- Le tableau de bord affiche les canaux actifs, la date du dernier envoi et son
  résultat (l'adresse est abrégée : `n***@famillelallier.net`).

### 7.3 Variables

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `HEAVEN_SMTP_HOST` / `HEAVEN_SMTP_PORT` | `smtp` / `25` | relais de l'Infra |
| `HEAVEN_NOTIFY_EMAIL_FROM` | `notifications@famillelallier.net` | expéditeur |
| `HEAVEN_NOTIFY_EMAIL_TO` | *(vide : courriel désactivé)* | destinataire |
| `HEAVEN_NTFY_URL` | `http://ntfy` | push |
| `HEAVEN_NTFY_TOKEN` | *(vide : push désactivé)* | jeton par application |
| `HEAVEN_NTFY_TOPIC` | `heaven-alertes` | sujet |
| `HEAVEN_NOTIFY_REPEAT` | `24h` | rappel d'un problème qui dure |
| `HEAVEN_NOTIFY_WEEKLY` | `mon@08:00` | résumé hebdomadaire ; vide pour le couper |

### 7.4 iPhone

iOS n'autorise pas une application à garder une connexion ouverte en arrière-
plan : un serveur ntfy auto-hébergé ne peut réveiller l'iPhone que par le
service de notification d'Apple (APNs). Or la config actuelle de l'Infra
(`ntfy/server.yml`) laisse `upstream-base-url` désactivé : sur iPhone, les
alertes n'arriveraient qu'en retard, voire pas, ce qui défait l'intérêt du
push pour un état rouge.

Prérequis côté **Infra** (hors de ce dépôt) :

- Activer `upstream-base-url: "https://ntfy.sh"`. Seul un identifiant de
  message (hachage du sujet, jamais le contenu) transite par ntfy.sh, qui sert
  de relais vers APNs. Le contenu des alertes reste sur le serveur de l'Infra.
- Sur l'iPhone : installer l'application ntfy, ajouter le serveur
  `https://ntfy.infra.famillelallier.net` avec le compte humain ntfy, puis
  s'abonner au sujet `heaven-alertes`. Accès hors du LAN : il faut un VPN (pas
  de ntfy exposé sur Internet, voir l'Infra).
- Pour les alertes **urgentes** (dépôt corrompu), la priorité `urgent` de ntfy
  permet de passer outre le mode « Ne pas déranger » si le réglage est activé
  dans l'application.

Le courriel reste le canal de secours si le push est en retard.

## 8. Critères d'acceptation

1. Avec un état `ok`, la page affiche l'indicateur vert, la date de la
   dernière sauvegarde et la prochaine échéance.
2. Après un cycle en échec, l'indicateur passe au rouge et affiche l'erreur
   du cycle, sans recharger la page (≤ 30 s).
3. Une sauvegarde en retard de plus d'une heure passe l'indicateur au rouge
   (même règle que la sonde Docker).
4. Un cycle avec fichiers ignorés passe l'indicateur à l'orange et liste les
   avertissements.
5. L'historique conserve au moins 30 cycles après redémarrage du conteneur
   (le fichier d'état est sur un volume).
6. Un `state.json` au `format: 1` est lu sans erreur.
7. Les routes restent en lecture seule : tout `POST`/`PUT`/`DELETE` est
   refusé (405).
8. Aucune requête n'est bloquée par le calcul de taille du dépôt (valeur
   en cache).
9. La page est lisible à 360 px de large.
10. Tests : `tests/test_api.py` couvre `/api/runs`, `/api/repository` et le
    champ `status` ; `tests/test_scheduler.py` couvre la migration du format
    d'état et la borne de l'historique.
11. Sans connexion Keycloak, ou sans le groupe `heaven`, l'interface est
    inaccessible ; une requête vers `heaven-web` sans l'en-tête `X-Heaven-Proxy`
    reçoit 403, même avec un en-tête d'identité ; l'API sans jeton de service
    valide reçoit 401.
12. Un cycle en échec envoie **un** courriel et **un** push ; un second cycle en
    échec ne renvoie rien avant 24 h ; le retour à l'état OK envoie un message
    de rétablissement. Le résumé hebdomadaire part le lundi même si tout va bien.
13. Une panne d'envoi (SMTP ou ntfy injoignable) n'interrompt ni le service ni
    le cycle en cours.
14. Sous `HEAVEN_DISK_WARN` l'état passe à l'orange, sous `HEAVEN_DISK_CRIT`
    au rouge.
15. On peut parcourir un instantané de 500 000 fichiers sans charger toute la
    liste dans le navigateur.
16. *(Étape 9, si elle est faite.)* Avec deux instances configurées, l'onglet « Toutes » affiche l'état des
    deux ; une instance injoignable apparaît en gris sans masquer l'autre.

## 9. Plan de livraison

| Étape | Contenu | Dépend de |
| --- | --- | --- |
| 1 | `state.json` v2 : historique borné + résultat de vérification | — |
| 2 | `/api/runs`, `/api/repository`, `status` dans `/api/health` | 1 |
| 3 | Authentification : client Keycloak, `oauth2-proxy` et vhost (**dépôt Infra**) ; contrôle de l'en-tête secret et du jeton de service dans `heaven-web` / l'API | — |
| 4 | Interface : bandeau d'état + cartes + liste des instantanés | 2, 3 |
| 5 | Interface : historique des cycles + courbe de taille + espace disque | 2, 4 |
| 6 | Notifications courriel via `smtp:25` (+ `heaven notify-test`, `heaven` sur `infra-net`) | 2 |
| 7 | Notifications push via ntfy (jeton `make ntfy-provision`) et résumé hebdomadaire | 6 |
| 8 | Détail d'un instantané : route d'API + arborescence | 3, 4 |
| 9 | Vue multi-instances *(facultative, repoussée : une seule machine)* | 3, 4 |
| 10 | Documentation (`README.md`, `docs/portainer.md`, `.env.example`) | toutes |

Chaque étape est livrable seule et ne casse pas la sonde de santé Docker.

## 10. Décisions

| # | Question | Décision |
| --- | --- | --- |
| 1 | Notifications | Courriel + push téléphone, via les services de l'Infra (§7) |
| 2 | Plusieurs dépôts / machines | Une seule machine aujourd'hui ; la vue agrégée (§3.8) est conservée mais **repoussée** (étape 9, facultative) |
| 3 | Seuil d'espace disque | Oui : `HEAVEN_DISK_WARN` / `HEAVEN_DISK_CRIT` (§3.7) |
| 4 | Détail d'un instantané | Oui : arborescence dans l'interface (§3.6) |
| 5 | Historique de 200 cycles | Oui (§5.2) |
| 6 | Authentification | Comptes distincts, via Keycloak (§6.1) ; seul membre du groupe `heaven` au départ : Nicolas Lallier |
| 7 | SMS ou push | Le push ntfy suffit ; pas de SMS (§7) |
| 8 | Serveur SMTP | Relais `smtp:25` de l'Infra |
| 9 | Destinataire des alertes | Une seule personne : `nicolas.lallier@famillelallier.net`, saisi dans `HEAVEN_NOTIFY_EMAIL_TO` (variable de la pile, vide = courriel désactivé) |
| 10 | Résumé hebdomadaire | Oui, activé par défaut, lundi 08:00 |
| 11 | Téléphone abonné au push | iPhone de Nicolas Lallier (voir §7.4) |
| 12 | Relais `ntfy.sh` pour l'iPhone | Accepté : `upstream-base-url` à activer dans l'Infra (§7.4), prérequis de l'étape 7 |

## 11. Questions ouvertes

1. **Multi-instances** : à reprendre le jour où une deuxième machine
   sauvegardée apparaît.
