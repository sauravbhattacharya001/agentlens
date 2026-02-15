# ── Stage 1: Build ────────────────────────────────────────────────────
# Install all dependencies including devDependencies, then prune to
# production-only for a smaller runtime image.
FROM node:22-alpine AS builder

WORKDIR /build

# Copy backend package files first for better layer caching
COPY backend/package.json backend/package-lock.json ./backend/

# Install all deps (including dev) — npm ci guarantees deterministic builds
RUN cd backend && npm ci --ignore-scripts

# Copy application source
COPY backend/ ./backend/
COPY dashboard/ ./dashboard/

# Prune devDependencies for the runtime layer
RUN cd backend && npm prune --production

# ── Stage 2: Runtime ─────────────────────────────────────────────────
FROM node:22-alpine

# Security: add non-root user
RUN addgroup -S agentlens && adduser -S agentlens -G agentlens

WORKDIR /app

# better-sqlite3 needs a few native libs on Alpine
RUN apk add --no-cache libstdc++

# Copy production deps and source from builder
COPY --from=builder /build/backend/ ./backend/
COPY --from=builder /build/dashboard/ ./dashboard/

# Data directory for SQLite — volume-mountable for persistence
RUN mkdir -p /app/data && chown -R agentlens:agentlens /app

# Environment defaults
ENV PORT=3000 \
    NODE_ENV=production

# Health check — uses the built-in /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:3000/health | grep -q '"status":"ok"' || exit 1

# Expose the default port
EXPOSE 3000

# Drop to non-root user
USER agentlens

# Start the backend server (which also serves the dashboard)
CMD ["node", "backend/server.js"]
