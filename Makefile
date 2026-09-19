# Makefile de Heaven — enveloppe les commandes de développement courantes.
#
# Toutes les cibles travaillent dans un environnement virtuel local ($(VENV)),
# créé et mis à jour automatiquement : `make test` suffit sur un dépôt neuf.

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
STAMP := $(VENV)/.install-stamp

# Dépôt d'origine ou de destination partagé par les cibles d'utilisation.
REPO   ?=
# Cible de `make restore` ; source de `make backup` (vide par défaut = config).
TARGET ?= /tmp/heaven-restauration
SOURCE ?=

.DEFAULT_GOAL := help

# --- Environnement -----------------------------------------------------------

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip

$(STAMP): pyproject.toml | $(BIN)/python
	$(BIN)/python -m pip install -e ".[dev]"
	touch $@

.PHONY: install
install: $(STAMP) ## Crée le venv et installe le projet en mode éditable
	@echo "Environnement prêt : $(VENV) (activer avec « source $(BIN)/activate »)"

# --- Qualité -----------------------------------------------------------------

.PHONY: lint
lint: $(STAMP) ## Analyse statique (ruff check)
	$(BIN)/ruff check .

.PHONY: format
format: $(STAMP) ## Reformate le code (ruff format)
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

.PHONY: format-check
format-check: $(STAMP) ## Vérifie le formatage sans rien modifier
	$(BIN)/ruff format --check .

.PHONY: test
test: $(STAMP) ## Lance la suite de tests (ARGS="-k motif" pour filtrer)
	$(BIN)/pytest $(ARGS)

.PHONY: check
check: lint format-check test ## Reproduit localement la CI (lint + format + tests)

# --- Utilisation ---------------------------------------------------------------

.PHONY: run
run: $(STAMP) ## Exécute la CLI (ARGS="backup --tag quotidien")
	$(BIN)/heaven $(ARGS)

.PHONY: backup
backup: $(STAMP) ## Sauvegarde SOURCE -> REPO (vide par défaut = la configuration)
ifeq ($(SOURCE),)
	$(BIN)/heaven backup $(if $(REPO),--repository $(REPO),) $(ARGS)
else
	@if [ -z "$(REPO)" ]; then echo "erreur : SOURCE = $(SOURCE) impose REPO ; ex. make backup REPO=./depot SOURCE=~/Docs" 1>&2; exit 1; fi
	$(BIN)/heaven backup --source $(SOURCE) --repository $(REPO) $(ARGS)
endif

.PHONY: snapshots
snapshots: $(STAMP) ## Liste les instantanés de REPO (par défaut : la configuration)
ifeq ($(REPO),)
	$(BIN)/heaven snapshots $(ARGS)
else
	$(BIN)/heaven snapshots --repository $(REPO) $(ARGS)
endif

.PHONY: restore
restore: $(STAMP) ## Restaure le dernier instantané de REPO vers TARGET
ifeq ($(REPO),)
	$(BIN)/heaven restore latest --target $(TARGET) $(ARGS)
else
	$(BIN)/heaven restore latest --repository $(REPO) --target $(TARGET) $(ARGS)
endif

.PHONY: verify
verify: $(STAMP) ## Contrôle l'intégrité de REPO (par défaut : la configuration)
ifeq ($(REPO),)
	$(BIN)/heaven verify $(ARGS)
else
	$(BIN)/heaven verify --repository $(REPO) $(ARGS)
endif

.PHONY: prune
prune: $(STAMP) ## Applique la rétention et supprime les objets orphelins de REPO
ifeq ($(REPO),)
	$(BIN)/heaven prune $(ARGS)
else
	$(BIN)/heaven prune --repository $(REPO) $(ARGS)
endif

# --- Conteneur ---------------------------------------------------------------

IMAGE ?= heaven-backup:latest

.PHONY: docker-build
docker-build: ## Construit l'image de l'appliance ($(IMAGE))
	docker build -t $(IMAGE) .

.PHONY: docker-up
docker-up: ## Démarre la pile docker-compose (lire .env.example au préalable)
	docker compose up -d --build

.PHONY: docker-down
docker-down: ## Arrête la pile docker-compose
	docker compose down

.PHONY: docker-logs
docker-logs: ## Suit les journaux de l'appliance
	docker compose logs -f heaven

.PHONY: docker-config
docker-config: ## Vérifie la syntaxe des piles compose et Portainer
	docker compose --env-file .env.example config --quiet
	docker compose -f deploy/portainer/stack.yml --env-file .env.example config --quiet
	GH_RUNNER_TOKEN=verification docker compose -f docker-compose.runner.yml config --quiet

# --- Déploiement -------------------------------------------------------------
#
# La pile tourne sous Portainer et se redéploie toute seule quand main bouge
# (.github/workflows/deploy.yml). Ces cibles font la même chose à la main, sur
# la même API : elles déploient toujours GitHub main, jamais le checkout local.
# Elles ont besoin de jq, de la socket Docker, et de PORTAINER_API_KEY dans
# .portainer.env. Voir docs/portainer.md.

.PHONY: deploy
deploy: ## Redéploie la pile par Portainer en retirant l'image à nouveau
	./scripts/portainer-stack.sh pull

.PHONY: deploy-norepull
deploy-norepull: ## Redéploie la pile sans retirer l'image (rare : l'image est la livraison)
	./scripts/portainer-stack.sh up

.PHONY: deploy-down
deploy-down: ## Arrête la pile déployée (les volumes, dépôt compris, restent)
	./scripts/portainer-stack.sh down

.PHONY: deploy-delete
deploy-delete: ## Retire la pile de Portainer (les volumes restent)
	./scripts/portainer-stack.sh delete

.PHONY: deploy-selftest
deploy-selftest: ## Contrôle le script de déploiement sans rien déployer
	./scripts/portainer-stack.sh selftest

# --- Runner CI ---------------------------------------------------------------
#
# Le runner auto-hébergé qui exécute le déploiement, dans son propre projet
# compose : un redéploiement recrée la pile « heaven », et un runner recréé en
# plein job ne rend jamais son résultat. --env-file .runner.env n'est pas
# cosmétique — sans lui, compose irait chercher GH_RUNNER_TOKEN dans .env, qui
# est remis à Portainer comme environnement de la pile.

RUNNER_COMPOSE = docker compose -f docker-compose.runner.yml --env-file .runner.env

.PHONY: runner-up
runner-up: ## Démarre le runner CI auto-hébergé (son propre projet compose)
	@test -f .runner.env || { echo "erreur : .runner.env absent — y écrire GH_RUNNER_TOKEN (PAT autorisé à enregistrer des runners) ; voir .env.example" 1>&2; exit 1; }
	$(RUNNER_COMPOSE) up -d
	@echo "Confirmer avec « make runner-logs » jusqu'à « Listening for Jobs »."

.PHONY: runner-down
runner-down: ## Arrête le runner CI (jamais en plein déploiement : le job ne rendrait rien)
	$(RUNNER_COMPOSE) down

.PHONY: runner-logs
runner-logs: ## Suit les journaux du runner CI
	$(RUNNER_COMPOSE) logs -f

# --- Distribution ------------------------------------------------------------

.PHONY: build
build: $(STAMP) ## Construit les artefacts de distribution dans dist/
	$(BIN)/python -m pip install --quiet --upgrade build
	$(BIN)/python -m build

# --- Nettoyage ---------------------------------------------------------------

.PHONY: clean
clean: ## Supprime les caches et artefacts de construction
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -name '*.egg-info' -prune -exec rm -rf {} +
	find . -name '__pycache__' -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete

.PHONY: distclean
distclean: clean ## Nettoyage complet, environnement virtuel compris
	rm -rf $(VENV)

# --- Aide --------------------------------------------------------------------

.PHONY: help
help: ## Affiche cette aide
	@echo "Cibles disponibles :"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
