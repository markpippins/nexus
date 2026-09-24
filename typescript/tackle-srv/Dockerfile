# syntax=docker/dockerfile:1
# tackle-srv — legacy REST server (TypeScript)
# Replaced by nexus-control-edge (Wave 3.5). Packaged for the survivable
# legacy tier per decision D-2026-08-15 (dockerize, not decommission).
# Build context is the typescript/ repo root so file:../heartbeat-client
# and file:../tackle-seeds resolve.
FROM node:20-bookworm AS build
WORKDIR /app/tackle-srv
COPY tackle-srv/package.json ./
COPY tackle-srv/package-lock.json ./
COPY heartbeat-client ../heartbeat-client
COPY tackle-seeds ../tackle-seeds
# npm ci: installs exactly the committed lock (lockfile batch 2). The old
# '--no-package-lock to dodge stale locks' rationale is gone — the committed
# lock is generated from this package.json (file: deps included) and npm ci
# fails loudly on drift. Both file: siblings are COPYed above so the lock's
# file: entries resolve.
RUN npm ci --no-audit --no-fund
# Build tackle-seeds file: dep so its dist/ exists at runtime (main -> ./dist/index.js).
# heartbeat-client dist is built further below alongside the tsc workaround.
# (tackle-seeds has no committed lock of its own — opt-in per #518.)
RUN cd ../tackle-seeds && npm install --no-package-lock && npm run build
COPY tackle-srv/tsconfig.json ./
COPY tackle-srv/src ./src
# Build the file deps' dist first: the root .dockerignore excludes **/dist,
# so without this tsc may type-check heartbeat-client's TS source (ESM
# import.meta under CommonJS -> TS1343).
WORKDIR /app/tackle-srv
RUN cd ../heartbeat-client && npm install --no-package-lock && npm run build
RUN npm run build
RUN npm prune --omit=dev

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app/tackle-srv
COPY --from=build /app/tackle-srv/package.json ./
COPY --from=build /app/tackle-srv/node_modules ./node_modules
COPY --from=build /app/tackle-srv/dist ./dist
COPY --from=build /app/heartbeat-client /app/heartbeat-client
COPY --from=build /app/tackle-seeds /app/tackle-seeds

ENV TACKLE_SRV_PORT=3410
EXPOSE 3410

# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "dist/index.js"]
