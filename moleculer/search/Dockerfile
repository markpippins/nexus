FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
# npm ci: installs exactly the committed lock (lockfile batch 2).
RUN npm ci --no-audit --no-fund

# Copy source code
COPY . .

# Build TypeScript
RUN npm run build

# Expose port
EXPOSE 4050

# Run the application
CMD ["npm", "start"]