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
- Authentification : l'accès est protégé par le réseau (nginx d'Infra), comme
  aujourd'hui.
- Notifications (courriel, push) : voir §9.

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
| **Attention** | orange | Dernier cycle `ok` mais avec avertissements (fichiers ignorés) ; ou première sauvegarde pas encore faite (`pending`) ; ou dépôt à plus de 90 % d'un seuil d'espace configuré |
| **Problème** | rouge | Dernier cycle `error` ; ou sauvegarde en retard (règle de `scheduler.health`, délai de grâce 1 h) ; ou dépôt corrompu ; ou API / service injoignable |
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
Tri par date décroissante. Lecture seule en v1 (la restauration reste en CLI ;
on affiche la commande `heaven restore <id>` copiable).

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

### 5.4 `GET /api/snapshots` *(inchangé)*

### 5.5 Erreurs

- `503` + `{"error": "..."}` tant que le service n'a rien écrit : l'interface
  affiche l'état « Inconnu », pas une erreur.
- `404` pour toute autre route (comportement actuel).

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

## 7. Critères d'acceptation

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

## 8. Plan de livraison

| Étape | Contenu | Dépend de |
| --- | --- | --- |
| 1 | `state.json` v2 : historique borné + résultat de vérification | — |
| 2 | `/api/runs`, `/api/repository`, `status` dans `/api/health` | 1 |
| 3 | Interface : bandeau d'état + cartes + liste des instantanés | 2 |
| 4 | Interface : historique des cycles + courbe de taille | 2 |
| 5 | Documentation (`README.md`, `docs/portainer.md`) | 3, 4 |

Chaque étape est livrable seule et ne casse pas la sonde de santé Docker.

## 9. Questions ouvertes

1. **Notifications** : veut-on être prévenu (courriel, ntfy, Telegram) en cas
   d'échec ou de retard, ou le tableau de bord suffit-il ?
2. **Plusieurs dépôts / plusieurs machines** : un seul serveur Heaven pour
   l'instant ; faut-il prévoir une vue agrégée ?
3. **Seuil d'espace disque** : afficher l'espace libre du volume du dépôt,
   avec une alerte configurable (`HEAVEN_DISK_WARN`) ?
4. **Détail d'un instantané** : parcourir son contenu (`heaven list`) depuis
   l'interface, ou rester en CLI ?
5. **Durée de conservation de l'historique** : 200 cycles (~7 mois à un cycle
   par jour) convient-il ?
6. **Authentification** : suffisante derrière le nginx d'Infra, ou à
   prévoir si l'interface sort un jour du LAN ?
