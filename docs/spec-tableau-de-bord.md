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
| U10 | « Suis-je prévenu si ça casse ? » | Courriel et SMS en cas d'échec, de retard ou de manque d'espace |
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

Configuration côté service web : variable `HEAVEN_INSTANCES`, liste
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

### 6.1 Authentification

Le tableau de bord n'est plus ouvert à tout le réseau.

- Un mot de passe unique (`HEAVEN_UI_PASSWORD`, stocké haché au démarrage)
  protège l'interface : page de connexion, cookie de session `HttpOnly`,
  `SameSite=Strict`, `Secure` derrière HTTPS, expiration au bout de 7 jours.
- L'API accepte aussi un jeton `Authorization: Bearer` (`HEAVEN_API_TOKEN`),
  utilisé par le relais multi-instances (§3.8) et les outils externes.
- La sonde de santé Docker (`heaven health`) lit le fichier d'état et ne passe
  pas par HTTP : elle n'est pas affectée.
- Limitation des essais de connexion (5 échecs / 5 min par adresse).
- Comparaisons de secrets à temps constant (`hmac.compare_digest`).
- Mots de passe et jetons ne vont **que** dans les variables d'environnement
  de la pile ou un secret Docker, jamais dans le dépôt.

L'authentification est implémentée dans l'API Python (pas seulement dans
nginx), pour rester vraie quelle que soit la façon d'exposer la pile.

## 7. Notifications

Le service planifié envoie un message quand l'état se dégrade, pas à chaque
cycle.

| Événement | Quand |
| --- | --- |
| Échec de sauvegarde | Un cycle finit en `error` |
| Retard | Sauvegarde non démarrée au-delà du délai de grâce |
| Dépôt corrompu | La vérification trouve des objets manquants ou corrompus |
| Espace disque | Passage sous `HEAVEN_DISK_WARN` / `HEAVEN_DISK_CRIT` |
| Rétablissement | Retour à l'état OK après un problème |
| Résumé hebdomadaire *(facultatif)* | Un courriel par semaine, même si tout va bien : prouve que la surveillance elle-même fonctionne |

Règles :

- **Anti-bruit** : un message par *changement d'état* (OK → problème, problème
  → OK), puis un rappel toutes les 24 h tant que le problème dure
  (`HEAVEN_NOTIFY_REPEAT`). Un problème qui dure ne spamme pas.
- **Courriel** : SMTP (`HEAVEN_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`,
  `HEAVEN_NOTIFY_EMAIL_FROM`, `HEAVEN_NOTIFY_EMAIL_TO`), STARTTLS obligatoire
  hors LAN. Module `smtplib` de la bibliothèque standard : le projet reste
  **sans dépendance externe**.
- **SMS** : par une passerelle HTTP configurable (`HEAVEN_NOTIFY_SMS_URL`,
  corps et en-têtes modèles avec `{message}` et `{to}`), pour ne pas
  dépendre d'un fournisseur. Message court (≤ 160 caractères) : état, machine,
  raison abrégée, lien vers le tableau de bord. Réservé aux états **rouges** ;
  l'orange part en courriel seulement.
- Un échec d'envoi ne doit jamais arrêter le service : il est journalisé et
  réessayé au cycle suivant.
- L'état des notifications envoyées (dernier état notifié, date) vit dans
  `state.json` pour survivre aux redémarrages.
- `heaven notify-test` envoie un message d'essai sur tous les canaux
  configurés.
- Le tableau de bord affiche les canaux actifs et la date du dernier envoi
  (sans afficher adresses ni numéros en entier : `n***@famille…`).

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
11. Sans connexion, l'interface et l'API répondent 401 (sauf la page de
    connexion) ; un mauvais mot de passe 6 fois de suite est bloqué.
12. Un cycle en échec envoie **un** courriel (et un SMS) ; un second cycle en
    échec ne renvoie rien avant 24 h ; le retour à l'état OK envoie un message
    de rétablissement.
13. Une panne d'envoi (SMTP injoignable) n'interrompt pas le service.
14. Sous `HEAVEN_DISK_WARN` l'état passe à l'orange, sous `HEAVEN_DISK_CRIT`
    au rouge.
15. On peut parcourir un instantané de 500 000 fichiers sans charger toute la
    liste dans le navigateur.
16. Avec deux instances configurées, l'onglet « Toutes » affiche l'état des
    deux ; une instance injoignable apparaît en gris sans masquer l'autre.

## 9. Plan de livraison

| Étape | Contenu | Dépend de |
| --- | --- | --- |
| 1 | `state.json` v2 : historique borné + résultat de vérification | — |
| 2 | `/api/runs`, `/api/repository`, `status` dans `/api/health` | 1 |
| 3 | Authentification (mot de passe, jeton, limitation d'essais) | — |
| 4 | Interface : bandeau d'état + cartes + liste des instantanés | 2, 3 |
| 5 | Interface : historique des cycles + courbe de taille + espace disque | 2, 4 |
| 6 | Notifications courriel (+ `heaven notify-test`) | 2 |
| 7 | Notifications SMS (passerelle) | 6 |
| 8 | Détail d'un instantané : route d'API + arborescence | 3, 4 |
| 9 | Vue multi-instances | 3, 4 |
| 10 | Documentation (`README.md`, `docs/portainer.md`, `.env.example`) | toutes |

Chaque étape est livrable seule et ne casse pas la sonde de santé Docker.

## 10. Décisions

| # | Question | Décision |
| --- | --- | --- |
| 1 | Notifications | Oui : courriel et SMS (§7) |
| 2 | Plusieurs dépôts / machines | Oui : vue agrégée (§3.8) |
| 3 | Seuil d'espace disque | Oui : `HEAVEN_DISK_WARN` / `HEAVEN_DISK_CRIT` (§3.7) |
| 4 | Détail d'un instantané | Oui : arborescence dans l'interface (§3.6) |
| 5 | Historique de 200 cycles | Oui (§5.2) |
| 6 | Authentification | Oui : mot de passe + jeton d'API (§6.1) |

## 11. Questions ouvertes

1. **Fournisseur SMS** : lequel utiliser (Twilio, Free Mobile, passerelle
   courriel→SMS de l'opérateur, autre) ? La spec le laisse configurable, mais
   les modèles de requête par défaut dépendent du choix.
2. **Serveur SMTP** : compte existant (domaine `famillelallier.net`, Gmail,
   autre) ? Détermine le port et le mode TLS par défaut.
3. **Destinataires** : une seule adresse / un seul numéro, ou plusieurs
   (famille) ?
4. **Résumé hebdomadaire** : l'activer par défaut ?
5. **Multi-instances** : combien de machines à court terme, et chacune
   exécute-t-elle déjà Heaven en mode `serve` ?
6. **Authentification** : un mot de passe partagé suffit-il, ou faut-il des
   comptes distincts ?
