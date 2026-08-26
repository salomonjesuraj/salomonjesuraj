# Sniper HUD production image -- Node build stage, Nginx serve stage.
# Same two-stage shape as every other build in this repo that produces
# a static artifact (there isn't one yet for a JS app, this is the
# first), and the same Nginx-serves-static-plus-reverse-proxies-api-and-ws
# pattern as services/dashboard/Dockerfile + nginx.conf.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3001
