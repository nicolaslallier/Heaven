# Image de l'appliance Heaven : la CLI plus le mode service planifié.
#
#   docker build -t heaven-backup:latest .
#   docker run --rm -v /donnees:/sources:ro -v heaven-repo:/repository heaven-backup:latest
#
# Le conteneur se configure entièrement par variables d'environnement
# (HEAVEN_SOURCES, HEAVEN_REPOSITORY, HEAVEN_SCHEDULE…) : aucun fichier de
# configuration n'est nécessaire. Voir docs/portainer.md.

FROM python:3.13-slim AS builder

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

FROM python:3.13-slim

LABEL org.opencontainers.image.title="Heaven" \
      org.opencontainers.image.description="Appliance de sauvegarde incrémentale à dépôt adressé par contenu" \
      org.opencontainers.image.source="https://github.com/nicolaslallier/heaven" \
      org.opencontainers.image.licenses="MIT"

# tzdata : sans lui, HEAVEN_SCHEDULE=02:30 serait interprété en UTC quel que soit TZ.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC \
    HEAVEN_SOURCES=/sources \
    HEAVEN_REPOSITORY=/repository \
    HEAVEN_STATE=/var/lib/heaven/state.json \
    HEAVEN_SCHEDULE=02:30 \
    HEAVEN_KEEP_LAST=7 \
    HEAVEN_KEEP_DAILY=7 \
    HEAVEN_KEEP_WEEKLY=4 \
    HEAVEN_KEEP_MONTHLY=6

# /sources n'est volontairement pas créé ici : s'il manque au démarrage, c'est
# que le volume n'a pas été monté, et « serve » le dit au lieu d'enregistrer des
# instantanés vides. Docker crée le point de montage tout seul quand il y en a un.
# (Créés avant VOLUME : après, Docker ignorerait ces modifications.)
RUN mkdir -p /repository /var/lib/heaven

# /sources est monté en lecture seule ; seuls le dépôt et l'état sont écrits.
VOLUME ["/repository", "/var/lib/heaven"]

# La sonde relit l'état écrit par « serve » : le conteneur passe en « unhealthy »
# si le dernier cycle a échoué ou si l'échéance est dépassée de plus d'une heure.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD ["heaven", "health"]

ENTRYPOINT ["heaven"]
CMD ["serve"]
