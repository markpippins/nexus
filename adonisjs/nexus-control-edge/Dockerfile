# nexus-control-edge — adonisjs v6 REST edge (candidate stack)
#
# NOTE: this app declares its framework packages (@adonisjs/*) under
# devDependencies, so the runtime stage must run a FULL `npm ci` from the
# root lockfile — `npm ci --omit=dev` inside build/ strips the framework.
FROM node:24-slim AS build
WORKDIR /src
# Lock files are gitignored in this repo, so a fresh clone has none and
# `COPY package-lock.json` fails the build (see moleculer/nexus-broker).
# The glob tolerates the absence; `npm ci` still runs when a lockfile is
# present, and the fallback resolves from package.json otherwise — which also
# materialises the lockfile the runtime stage below copies.
COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY . .
RUN node ace build

FROM node:24-slim
WORKDIR /app
COPY --from=build /src/package.json /src/package-lock.json ./
RUN npm ci
COPY --from=build /src/build ./build

ENV NODE_ENV=production HOST=0.0.0.0 PORT=8082
EXPOSE 8082
CMD ["node", "build/bin/server.js"]
