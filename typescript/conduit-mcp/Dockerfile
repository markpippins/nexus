# syntax=docker/dockerfile:1
# conduit-mcp — receipt-first pipeline authority MCP (streamable HTTP, :3100).
# Packaged for the vanadium deployment per architect ruling X2 (2026-08-23):
# documented ports are authoritative — the transport matrix assigns :3100 to
# conduit-mcp (GET /state, JSON-RPC POST /); conduit-srv REST stays on :3104.
FROM node:20-bookworm AS build
WORKDIR /app/conduit-mcp
COPY conduit-mcp/package.json ./
COPY conduit-mcp/package-lock.json ./
# npm ci: installs exactly the committed lock (lockfile batch 2).
RUN npm ci --no-audit --no-fund
COPY conduit-mcp/tsconfig.json ./
COPY conduit-mcp/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app/conduit-mcp
COPY --from=build /app/conduit-mcp/package.json ./
COPY --from=build /app/conduit-mcp/node_modules ./node_modules
COPY --from=build /app/conduit-mcp/dist ./dist

ENV PORT=3100
EXPOSE 3100

# Drop privileges: the base image runs as root by default.
RUN chown -R node:node /app
USER node
CMD ["node", "dist/index.js"]
