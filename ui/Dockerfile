# Command Center production image -- Node build stage, Nginx serve stage.
# Same two-stage shape as every other build in this repo that produces
# a static artifact, and the same Nginx-serves-static-plus-reverse-
# proxies-api-and-ws pattern the deleted legacy services/dashboard/
# used. This is now the sole frontend, on port 3000 (2026-08-27 --
# the legacy dashboard that used to own that port was removed outright).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3000
