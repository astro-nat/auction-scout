# Multi-stage: `dev` runs Vite's dev server (used by docker-compose locally);
# the final stage builds static files and serves them with nginx (Railway).

FROM node:20-slim AS base
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .

FROM base AS dev
CMD ["npm", "run", "dev"]

FROM base AS build
# Vite bakes env vars into the bundle at BUILD time. Railway passes service
# variables to Docker builds when declared as ARG — set VITE_API_BASE to the
# backend's public URL in the Railway frontend service.
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# nginx's official image envsubsts *.template into conf.d at startup —
# that's how we listen on Railway's assigned $PORT.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV PORT=80
