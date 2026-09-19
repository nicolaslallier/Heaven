# Image de l'interface web de Heaven : le bundle Vue, servi par nginx.
#
#   docker build -t heaven-web web
#   docker run --rm -p 8080:80 heaven-web
#
# L'interface est entièrement statique — aucune variable d'environnement, aucun
# volume. Elle se déploie à côté de l'appliance : voir docs/portainer.md.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine

LABEL org.opencontainers.image.title="Heaven — interface web" \
      org.opencontainers.image.description="Interface de gestion des sauvegardes Heaven" \
      org.opencontainers.image.source="https://github.com/nicolaslallier/heaven" \
      org.opencontainers.image.licenses="MIT"

COPY --from=build /app/dist /usr/share/nginx/html

# La sonde interroge la page servie : le conteneur passe en « unhealthy » si
# nginx ne répond plus, ce que Portainer affiche comme pour l'appliance.
HEALTHCHECK --interval=1m --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "--quiet", "--spider", "http://127.0.0.1/"]
