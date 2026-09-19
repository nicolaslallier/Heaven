#!/usr/bin/env bash
# Déploie la pile depuis la CI. .github/workflows/deploy.yml l'exécute sur le
# runner auto-hébergé une fois l'image publiée sur GHCR.
#
# Le travail tourne dans un conteneur docker:*-cli jetable, et non sur le runner
# lui-même, pour la raison qui fait tourner curl dans un conteneur dans
# portainer-stack.sh : l'image du runner ne porte alors aucune partie du
# déploiement, et la remplacer ou la mettre à jour ne peut pas changer en
# silence ce qui est déployé. Tout ce dont le script a besoin est installé dans
# ce conteneur, jamais supposé présent.
#
# Le checkout est monté *au même chemin* dedans que dehors. C'est ce qui en fait
# un chemin que le démon sait résoudre lui aussi — et un montage imbriqué est
# résolu par le démon, pas par le conteneur qui le demande. C'est pourquoi
# docker-compose.runner.yml pose RUNNER_WORKDIR sur un chemin hôte monté à
# l'identique plutôt que sur un volume nommé : un volume nommé donnerait ici un
# répertoire vide, et silencieusement.
#
# Usage : scripts/ci-deploy.sh [pull|up|down]
set -euo pipefail
cd "$(dirname "$0")/.."

# Épinglée : rien en amont ne force cette image à avancer, et c'est la chaîne
# d'outils sur laquelle le déploiement s'exécute réellement.
DEPLOY_IMAGE="${DEPLOY_IMAGE:-docker:28.5.2-cli}"

die() { printf 'ci-deploy.sh : %s\n' "$*" >&2; exit 1; }

cmd="${1:-pull}"
case "$cmd" in
  pull|up|down) ;;
  *) die "usage : scripts/ci-deploy.sh [pull|up|down]" ;;
esac

: "${PORTAINER_API_KEY:?PORTAINER_API_KEY manquante — la poser en secret du dépôt (Settings → Secrets and variables → Actions)}"

echo "ci-deploy.sh : $cmd depuis $PWD (chaîne de déploiement $DEPLOY_IMAGE)"

# Le script exécuté dans le conteneur jetable. Heredoc entre quotes : rien n'est
# développé ici, tout l'est dans le conteneur.
inner="$(cat <<'INNER'
# Installe seulement ce qui manque, pour ne pas masquer d'un second exemplaire
# un outil que l'image de base fournit déjà.
missing=""
for t in bash jq; do
  command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
[ -z "$missing" ] || apk add --no-cache $missing >/dev/null

# Docker crée la source manquante d'un montage au lieu de refuser : un workspace
# que le démon ne sait pas résoudre arrive donc comme un répertoire vide, pas
# comme une erreur. Le nommer ici, plutôt que de laisser « fichier introuvable »
# le raconter de travers.
[ -f scripts/portainer-stack.sh ] || {
  echo "ci-deploy.sh : $PWD ne contient pas scripts/portainer-stack.sh — le workspace du runner n'est pas visible du démon, qui a monté un répertoire vide à sa place. Vérifier RUNNER_WORKDIR dans docker-compose.runner.yml : ce doit être un chemin hôte monté au même chemin." >&2
  exit 1
}

exec bash scripts/portainer-stack.sh "$1"
INNER
)"

# PORTAINER_API_KEY est transmise par son nom, pas par sa valeur : elle
# n'apparaît sur aucune ligne de commande. portainer-stack.sh la fait ensuite
# passer à curl par l'entrée standard, jamais en argument non plus.
exec docker run --rm \
  -v "$PWD:$PWD" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w "$PWD" \
  -e DOCKER_HOST=unix:///var/run/docker.sock \
  -e PORTAINER_API_KEY \
  -e PORTAINER_NETWORK \
  -e PORTAINER_URL \
  -e PORTAINER_ENDPOINT_ID \
  -e HEAVEN_STACK_ENV \
  -e HEAVEN_STACK_NAME \
  -e HEAVEN_STACK_REF \
  "$DEPLOY_IMAGE" sh -euc "$inner" sh "$cmd"
