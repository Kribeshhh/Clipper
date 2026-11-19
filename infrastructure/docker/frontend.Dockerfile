FROM node:22-bookworm-slim AS dev
WORKDIR /app
RUN chown node:node /app
USER node
COPY --chown=node:node frontend/package*.json ./
RUN npm ci
COPY --chown=node:node frontend/ .
CMD ["npm","run","dev","--","--host","0.0.0.0"]
FROM dev AS build
RUN npm run build
FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
USER root
RUN apk upgrade --no-cache
COPY infrastructure/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101:101
