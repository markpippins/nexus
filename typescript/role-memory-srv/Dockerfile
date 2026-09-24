# syntax=docker/dockerfile:1
# role-memory-srv — legacy role-memory server (TypeScript)
# Replaced by nexus-control-edge role-memory routes (Wave 1.2). Packaged for
# the survivable legacy tier per decision D-2026-08-15 (dockerize, not decommission).
# Build context is the typescript/ repo root.
FROM node:20-bookworm AS build
WORKDIR /app/role-memory-srv
COPY role-memory-srv/package.json ./
COPY role-memory-srv/package-lock.json ./
# npm ci: installs exactly the committed lock (lockfile batch 2).
RUN npm ci --no-audit --no-fund
COPY role-memory-srv/tsconfig.json ./
COPY role-memory-srv/src ./src
# __tests__ is excluded by the root .dockerignore; guard in case the
# context changes — tests must not ship in the image.
RUN rm -rf src/__tests__
RUN npm run build
RUN npm prune --omit=dev

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app/role-memory-srv
COPY --from=build /app/role-memory-srv/package.json ./
COPY --from=build /app/role-memory-srv/node_modules ./node_modules
COPY --from=build /app/role-memory-srv/dist ./dist

ENV MEMORY_SRV_PORT=3500
EXPOSE 3500

# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "dist/index.js"]
