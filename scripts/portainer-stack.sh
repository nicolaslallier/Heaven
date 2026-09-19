#!/usr/bin/env bash
# Pilote la pile « heaven » par l'API de Portainer.
#
# Portainer déploie « deploy/portainer/stack.yml » depuis GitHub main, et cette
# pile tire une image déjà publiée sur GHCR. Aucun fichier du dépôt n'est monté
# dans le conteneur : tout ce qui est déployé vient de sources publiées, jamais
# d'une copie locale. C'est ce qui dispense ce script du garde-fou de dérive que
# porte le même script dans Infra — il n'y a pas de checkout hôte à tenir à jour.
#
# Usage : scripts/portainer-stack.sh up|pull|down|delete|selftest
#   up       crée la pile, ou la redéploie sur la référence courante
#   pull     redéploie en retirant l'image ; c'est ce que fait la CI, parce que
#            l'image *est* la livraison : « latest » a déjà bougé quand ce
#            script s'exécute, et un redéploiement sans retrait relancerait
#            l'ancienne image sans rien signaler
#   down     arrête la pile — les volumes restent, le dépôt de sauvegarde avec
#   delete   retire la pile de Portainer — les volumes restent, là aussi
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${HEAVEN_STACK_NAME:-heaven}"
REPO_URL="${HEAVEN_REPO_URL:-https://github.com/nicolaslallier/Heaven}"
REF="${HEAVEN_STACK_REF:-refs/heads/main}"
COMPOSE_FILE="${HEAVEN_COMPOSE_FILE:-deploy/portainer/stack.yml}"
CURL_IMAGE="${CURL_IMAGE:-curlimages/curl:8.5.0}"

# Portainer n'expose son API que sur le LAN et sur son propre réseau Docker : il
# n'y a aucune ingress publique vers elle, et c'est la raison d'être du runner
# auto-hébergé. Le conteneur curl se branche donc sur ce réseau. Les valeurs par
# défaut sont celles de la pile Infra, qui héberge ce Portainer.
PORTAINER_NETWORK="${PORTAINER_NETWORK:-infra-net}"
PORTAINER_URL="${PORTAINER_URL:-https://portainer:9443}"

# Variables de la pile : le contenu de HEAVEN_STACK_ENV s'il est renseigné (la
# CI y verse la variable de dépôt du même nom), sinon un .env local. Aucune des
# deux n'est obligatoire : stack.yml porte une valeur par défaut pour chaque
# clé, et une pile déployée sans variables sauvegarde /srv à 02:30.
ENV_FILE="${HEAVEN_ENV_FILE:-.env}"

die() { printf 'portainer-stack.sh : %b\n' "$*" >&2; exit 1; }

# Lit des lignes KEY=VALUE sur l'entrée standard et rend le [{name,value}]
# attendu par Portainer. Les réglages PORTAINER_* sont écartés : ils pilotent ce
# script et n'ont rien à faire dans l'environnement d'un conteneur.
env_json() {
  jq -Rn '
    [inputs
     | sub("\r$"; "")
     | select(test("^[A-Za-z_][A-Za-z0-9_]*="))
     | capture("^(?<name>[^=]+)=(?<value>.*)$")
     | select((.name | startswith("PORTAINER_")) | not)]'
}

stack_env_lines() {
  if [ -n "${HEAVEN_STACK_ENV:-}" ]; then
    printf '%s\n' "$HEAVEN_STACK_ENV"
  elif [ -f "$ENV_FILE" ]; then
    cat "$ENV_FILE"
  fi
}

selftest() {
  local got want
  # La dernière ligne est en CRLF : une variable de dépôt se saisit dans un
  # textarea, et rien n'y garantit des fins de ligne Unix.
  got="$({ printf '%s\n' '# commentaire' '' 'HEAVEN_SCHEDULE=02:30' \
    'HEAVEN_EXCLUDES=*.tmp,__pycache__' 'VIDE=' 'PORTAINER_API_KEY=secret' \
    '  INDENTE=non'; printf 'TZ=Europe/Paris\r\n'; } | env_json | jq -c .)"
  want='[{"name":"HEAVEN_SCHEDULE","value":"02:30"},{"name":"HEAVEN_EXCLUDES","value":"*.tmp,__pycache__"},{"name":"VIDE","value":""},{"name":"TZ","value":"Europe/Paris"}]'
  [ "$got" = "$want" ] || die "selftest : env_json\n  obtenu : $got\n  attendu : $want"
  echo "portainer-stack.sh : selftest ok"
}

# curl tourne dans un conteneur jetable posé sur le réseau de Portainer : le
# déploiement ne dépend donc d'aucun outil installé sur la machine qui l'appelle.
# La clé arrive par -K (un fichier de configuration écrit dans le conteneur
# depuis la première ligne de l'entrée standard, le corps suivant), jamais en
# argument de ligne de commande : elle n'apparaît dans aucune liste de processus.
api() { # <méthode> <chemin> [corps-json]
  # MSYS_NO_PATHCONV/MSYS2_ARG_CONV_EXCL : sous Git pour Windows, « /endpoints »
  # serait réécrit en chemin Windows avant d'atteindre docker.exe. Sans effet
  # ailleurs.
  printf '%s\n%s' "$PORTAINER_API_KEY" "${3:-}" | MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
    docker run --rm -i --network "$PORTAINER_NETWORK" \
    --entrypoint sh "$CURL_IMAGE" -c '
      IFS= read -r key
      printf "header = \"X-API-Key: %s\"\n" "$key" >/tmp/curl.cfg
      out="$(curl -sSk -K /tmp/curl.cfg --fail-with-body -X "$1" \
        -H "Content-Type: application/json" \
        --data-binary @- "$3/api$2" 2>&1)" \
        || { printf "%s\n" "$out" >&2; exit 1; }
      printf "%s" "$out"' sh "$1" "$2" "$PORTAINER_URL" \
    || die "$1 $2 a échoué — Portainer répond-il sur $PORTAINER_URL, et le réseau « $PORTAINER_NETWORK » existe-t-il ?"
}

cmd="${1:-}"
case "$cmd" in
  selftest) selftest; exit 0 ;;
  up|pull|down|delete) ;;
  *) die "usage : scripts/portainer-stack.sh up|pull|down|delete|selftest" ;;
esac

command -v jq >/dev/null 2>&1 || die "jq est requis (les corps de requête sont construits avec)"
command -v docker >/dev/null 2>&1 || die "la commande docker est requise"

# La clé d'API est un jeton root sur le démon Docker : elle vit dans son propre
# fichier ignoré par git, jamais dans .env, qui est remis tel quel à Portainer
# comme environnement de la pile. En CI, elle arrive par le secret du dépôt.
if [ -z "${PORTAINER_API_KEY:-}" ] && [ -f .portainer.env ]; then
  set -a; . ./.portainer.env; set +a
fi
if [ -z "${PORTAINER_API_KEY:-}" ] || [ "$PORTAINER_API_KEY" = change-me ]; then
  die "PORTAINER_API_KEY n'est pas renseignée — la créer dans Portainer (My account → Access tokens),\n  puis l'écrire dans .portainer.env en local, ou dans le secret du dépôt pour la CI (voir docs/portainer.md)"
fi

eid="${PORTAINER_ENDPOINT_ID:-$(api GET /endpoints | jq -r '[.[] | select(.Type == 1)][0].Id // empty')}"
[ -n "$eid" ] || die "aucun environnement Docker local trouvé dans Portainer"
stack="$(api GET /stacks | jq -c --arg n "$STACK" 'first(.[] | select(.Name == $n)) // empty')"
sid=""
[ -z "$stack" ] || sid="$(jq -r .Id <<<"$stack")"

case "$cmd" in
  up|pull)
    env="$(stack_env_lines | env_json)"
    # Sans variables fournies, reprendre celles que la pile porte déjà. Envoyer
    # un tableau vide les effacerait : la pile retomberait sur les valeurs par
    # défaut de stack.yml et sauvegarderait /srv au lieu des vraies sources,
    # sans rien signaler. « Aucune variable fournie » veut dire « ne touche pas
    # aux variables », jamais « efface-les ».
    if [ "$env" = "[]" ] && [ -n "$stack" ]; then
      env="$(jq -c '.Env // []' <<<"$stack")"
      [ "$env" = "[]" ] || echo "portainer-stack.sh : aucune variable fournie — les $(jq length <<<"$env") variables déjà posées sur la pile sont conservées"
    fi
    if [ -z "$sid" ]; then
      body="$(jq -n --arg name "$STACK" --arg url "$REPO_URL" --arg ref "$REF" \
        --arg compose "$COMPOSE_FILE" --argjson env "$env" \
        '{Name: $name, RepositoryURL: $url, RepositoryReferenceName: $ref,
          ComposeFile: $compose, RepositoryAuthentication: false, Env: $env}')"
      api POST "/stacks/create/standalone/repository?endpointId=$eid" "$body" >/dev/null
      echo "portainer-stack.sh : pile « $STACK » créée depuis $REPO_URL ($REF, $COMPOSE_FILE)"
    else
      # Statut 2 : pile arrêtée. La redéployer sans la redémarrer la laisserait
      # à jour et éteinte, ce qui n'est jamais ce qu'on veut d'un déploiement.
      if [ "$(jq -r .Status <<<"$stack")" = 2 ]; then
        api POST "/stacks/$sid/start?endpointId=$eid" >/dev/null
      fi
      # PullImage et RepullImageAndRedeploy portent la même intention : le champ
      # a été renommé en cours de route, et Portainer ignore en silence celui
      # qu'il ne connaît pas. Envoyer les deux, plutôt que de laisser un
      # redéploiement relancer l'ancienne image sans le dire.
      body="$(jq -n --arg ref "$REF" --argjson env "$env" \
        --argjson pull "$([ "$cmd" = pull ] && echo true || echo false)" \
        '{RepositoryReferenceName: $ref, RepositoryAuthentication: false, Env: $env,
          Prune: false, PullImage: $pull, RepullImageAndRedeploy: $pull}')"
      api PUT "/stacks/$sid/git/redeploy?endpointId=$eid" "$body" >/dev/null
      echo "portainer-stack.sh : pile « $STACK » redéployée sur $REF$([ "$cmd" = pull ] && echo ' (image retirée à nouveau)')"
    fi
    ;;
  down)
    [ -n "$sid" ] || die "la pile « $STACK » n'existe pas dans Portainer"
    if [ "$(jq -r .Status <<<"$stack")" = 2 ]; then
      echo "portainer-stack.sh : la pile « $STACK » est déjà arrêtée"
      exit 0
    fi
    api POST "/stacks/$sid/stop?endpointId=$eid" >/dev/null
    echo "portainer-stack.sh : pile « $STACK » arrêtée (volumes conservés)"
    ;;
  delete)
    [ -n "$sid" ] || { echo "portainer-stack.sh : aucune pile « $STACK » dans Portainer"; exit 0; }
    api DELETE "/stacks/$sid?endpointId=$eid" >/dev/null
    echo "portainer-stack.sh : pile « $STACK » retirée de Portainer (volumes conservés)"
    ;;
esac
