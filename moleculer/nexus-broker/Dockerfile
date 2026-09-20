# nexus-broker — moleculer worker tier (candidate stack)
# transporter: null → fully in-process; no NATS/Redis needed for the broker itself.
FROM node:24-slim
WORKDIR /app

# node-pty builds natively — needs the toolchain on slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

# This repo does not track lock files (.gitignore, "Lock files"), so a fresh
# clone has no package-lock.json and `COPY package.json package-lock.json`
# fails the build outright. Discovered 2026-09-20 on the first non-titanium
# (helium) deploy: local builds only ever worked because the dev checkout
# happened to hold an untracked lockfile.
# A glob keeps the COPY valid when the lock is absent, and `npm ci` still runs
# wherever a lockfile exists (so existing local builds are unchanged); the
# fallback resolves from package.json when there is none.
# (The durable fix — tracking lock files — changes repo-wide policy and needs
# an owner's ruling, not a unilateral edit here.)
COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY tsconfig.json moleculer.config.ts ./
COPY services ./services
RUN npx tsc

ENV NODE_ENV=production SERVICE_PORT=4080
EXPOSE 4080
CMD ["npx", "moleculer-runner", "--mask", "**/*.js", "dist/services"]
