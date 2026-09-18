# Makefile de Heaven — enveloppe les commandes de développement courantes.
#
# Toutes les cibles travaillent dans un environnement virtuel local ($(VENV)),
# créé et mis à jour automatiquement : `make test` suffit sur un dépôt neuf.

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
STAMP := $(VENV)/.install-stamp

# Répertoire cible de `make restore` / source de `make backup`.
TARGET ?= /tmp/heaven-restauration
SOURCE ?= .

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
backup: $(STAMP) ## Sauvegarde SOURCE (défaut : le répertoire courant)
	$(BIN)/heaven backup --source $(SOURCE)

.PHONY: snapshots
snapshots: $(STAMP) ## Liste les instantanés du dépôt configuré
	$(BIN)/heaven snapshots

.PHONY: restore
restore: $(STAMP) ## Restaure le dernier instantané vers TARGET
	$(BIN)/heaven restore latest --target $(TARGET)

.PHONY: verify
verify: $(STAMP) ## Contrôle l'intégrité du dépôt
	$(BIN)/heaven verify

.PHONY: prune
prune: $(STAMP) ## Applique la rétention et supprime les objets orphelins
	$(BIN)/heaven prune

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
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
