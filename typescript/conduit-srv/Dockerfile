# syntax=docker/dockerfile:1
# conduit-srv — TypeScript REST server (tsc build).
# Build context is the typescript/ repo root so file:../heartbeat-client resolves.
FROM node:20-bookworm AS build
WORKDIR /app/conduit-srv

# Lockfiles are TRACKED (38M priority 1a). Previously `npm install --no-package-lock`,
# which tells npm to ignore any lockfile present -- so a tracked lockfile would not
# have been read, and the image would still float. `npm ci` reads the committed lock.
COPY conduit-srv/package.json conduit-srv/package-lock.json ./
COPY heartbeat-client ../heartbeat-client
RUN npm ci --include=dev --no-audit --no-fund
# Build file: sibling so its dist/ exists at runtime (main -> ./dist/index.js)
# --include=dev kept explicit: `npm run build` here is tsc, which lives in devDeps.
# Build file: sibling so its dist/ exists at runtime (main -> ./dist/index.js)
# --include=dev is load-bearing for wind-srv: ENV NODE_ENV=production precedes
# this RUN, and without it npm drops the devDeps that `tsc` needs.
#
# Still `npm install --no-package-lock` -- deliberately UNCHANGED. heartbeat-client is a
# file: build dep of 22 services, so tracking ITS lockfile in a PR scoped to two
# services would make 9 unrelated Dockerfiles and 4 workflows violate wf-lint's
# npm-ci rule. That is a shared-dependency decision, not a P1a one; deferred.
RUN cd ../heartbeat-client && npm install --no-package-lock --include=dev && npm run build

COPY conduit-srv/ ./
RUN npm run build

FROM node:20-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app/conduit-srv

COPY --from=build /app/conduit-srv/package.json ./
COPY --from=build /app/conduit-srv/node_modules ./node_modules
COPY --from=build /app/conduit-srv/dist ./dist
COPY --from=build /app/heartbeat-client /app/heartbeat-client

ENV CONDUIT_SRV_PORT=3104
EXPOSE 3104

# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "dist/index.js"]
