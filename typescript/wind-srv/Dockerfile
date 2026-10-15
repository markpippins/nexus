# syntax=docker/dockerfile:1
# wind-srv — legacy REST server (plain JS, no build step)
# Replaced by nexus-control-edge (Wave 3.4). Packaged for the survivable
# legacy tier per decision D-2026-08-15 (dockerize, not decommission).
# Build context is the typescript/ repo root so file:../heartbeat-client resolves.
FROM node:20-bookworm-slim

ENV NODE_ENV=production
WORKDIR /app/wind-srv

# Lockfiles are TRACKED (38M priority 1a). These lines previously used
# `npm install --no-package-lock`, which makes npm IGNORE any lockfile present --
# so tracking one would have changed nothing about what the image actually
# installed. `npm ci` reads the committed lock and fails loudly if it is missing
# or out of sync, instead of quietly re-resolving floating ranges.
COPY wind-srv/package.json wind-srv/package-lock.json ./
COPY heartbeat-client ../heartbeat-client
RUN npm ci --omit=dev --no-audit --no-fund
# Build file: sibling so its dist/ exists at runtime (main -> ./dist/index.js)
# --include=dev is load-bearing: ENV NODE_ENV=production is set above, and without
# it npm omits devDependencies, taking the `tsc` that builds this file with them.
RUN cd ../heartbeat-client && npm ci --include=dev --no-audit --no-fund && npm run build

COPY wind-srv/src ./src

ENV WIND_SRV_PORT=3300
EXPOSE 3300

# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "src/index.js"]
