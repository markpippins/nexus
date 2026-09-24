# syntax=docker/dockerfile:1
# execution-srv — legacy observability server (TypeScript)
# Replaced by worker.execution in nexus-broker (Wave 4). Packaged for the
# survivable legacy tier per decision D-2026-08-15 (dockerize, not decommission).
# Build context is the typescript/ repo root so file:../heartbeat-client resolves.
FROM node:20-bookworm AS build
WORKDIR /app/execution-srv
COPY execution-srv/package.json ./
COPY execution-srv/package-lock.json ./
COPY heartbeat-client ../heartbeat-client
# npm ci: installs exactly the committed lock (lockfile batch 2); the file:
# sibling is COPYed above so the lock's file: entry resolves.
RUN npm ci --no-audit --no-fund
# Build file: sibling so its dist/ exists at runtime (main -> ./dist/index.js).
# heartbeat-client has no committed lock of its own (opt-in per #518).
RUN cd ../heartbeat-client && npm install --no-package-lock && npm run build
COPY execution-srv/tsconfig.json ./
COPY execution-srv/src ./src
RUN npm run build
RUN npm prune --omit=dev

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app/execution-srv
COPY --from=build /app/execution-srv/package.json ./
COPY --from=build /app/execution-srv/node_modules ./node_modules
COPY --from=build /app/execution-srv/dist ./dist
COPY --from=build /app/heartbeat-client /app/heartbeat-client

ENV PORT=3110
EXPOSE 3110

# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "dist/index.js"]
