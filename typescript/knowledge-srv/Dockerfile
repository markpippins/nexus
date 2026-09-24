# syntax=docker/dockerfile:1
# Build context is the typescript/ repo root so file:../heartbeat-client resolves.
# Vanadium failover tier per operator scope 2026-08-22 (.directory-marked).
FROM node:20-bookworm AS build
WORKDIR /app/knowledge-srv
COPY knowledge-srv/package.json ./
COPY knowledge-srv/package-lock.json ./
COPY heartbeat-client ../heartbeat-client
# npm ci: installs exactly the committed lock (lockfile batch 2); the file:
# sibling is COPYed above so the lock's file: entry resolves.
RUN npm ci --no-audit --no-fund
# heartbeat-client has no committed lock of its own (opt-in per #518).
RUN cd ../heartbeat-client && npm install --no-package-lock --include=dev && npm run build
COPY knowledge-srv/tsconfig.json ./
COPY knowledge-srv/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app/knowledge-srv
COPY --from=build /app/knowledge-srv/package.json ./
COPY --from=build /app/knowledge-srv/node_modules ./node_modules
COPY --from=build /app/knowledge-srv/dist ./dist
COPY --from=build /app/heartbeat-client /app/heartbeat-client
EXPOSE 3109
# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "dist/index.js"]
