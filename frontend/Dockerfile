# Pin the base image by digest (tag kept for readability) for reproducible builds.
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS deps

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM deps AS dev
COPY frontend/ ./
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

FROM deps AS build
ARG VITE_API_BASE_URL
ARG VITE_GITHUB_REPOSITORY_URL
ARG VITE_GITHUB_RELEASES_URL
ARG VITE_GITHUB_ISSUES_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_GITHUB_REPOSITORY_URL=${VITE_GITHUB_REPOSITORY_URL} \
    VITE_GITHUB_RELEASES_URL=${VITE_GITHUB_RELEASES_URL} \
    VITE_GITHUB_ISSUES_URL=${VITE_GITHUB_ISSUES_URL}
COPY frontend/ ./
RUN npm run build

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS production
ENV NODE_ENV=production
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# server.mjs imports only express. The other `dependencies` are the libraries bundled into
# dist/ (react, d3, igv, ...); they stay there so the blocking production audit covers what
# ships to the browser. The build toolchain (vite, typescript, @types/*) is in
# devDependencies and is not installed here (#530).
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
COPY --from=build /app/server.mjs ./server.mjs
COPY --from=build /app/proxyLocation.mjs ./proxyLocation.mjs
COPY --from=build /app/proxyRequest.mjs ./proxyRequest.mjs
COPY --from=build /app/securityHeaders.mjs ./securityHeaders.mjs
# Drop to the unprivileged built-in node user (least privilege). The frontend
# container serves static assets + proxies the API and has no writable bind mounts,
# so non-root is safe here. (The backend container now also runs non-root; local
# docker-compose overrides `user: root` for its host-owned /data bind mount.)
RUN chown -R node:node /app
USER node
EXPOSE 3000
CMD ["node", "server.mjs"]
