# nexus-control-edge — adonisjs v6 REST edge (candidate stack)
#
# NOTE: this app declares its framework packages (@adonisjs/*) under
# devDependencies, so the runtime stage must run a FULL `npm ci` from the
# root lockfile — `npm ci --omit=dev` inside build/ strips the framework.
FROM node:24-slim AS build
WORKDIR /src
# Lockfile is TRACKED here (38L priority 1 -- this service is deployed on :8082
# from this tree, and its @adonisjs/* runtime deps are floating devDependencies).
# This build previously carried `COPY package*.json` plus an
# `npm ci || npm install` fallback, which existed only to tolerate the
# repo-wide lockfile ignore. With the lock committed, that fallback is not a
# convenience -- it is the reproducibility hole: any lock drift would silently
# re-resolve floating ranges and still produce a "successful" image. So the
# lock is now copied explicitly and `npm ci` is the only path.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
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
