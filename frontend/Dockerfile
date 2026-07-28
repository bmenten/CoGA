# Pin the base image by digest (tag kept for readability) for reproducible builds.
FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS deps

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

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS production
ENV NODE_ENV=production
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
COPY --from=build /app/server.mjs ./server.mjs
COPY --from=build /app/proxyLocation.mjs ./proxyLocation.mjs
COPY --from=build /app/securityHeaders.mjs ./securityHeaders.mjs
# Drop to the unprivileged built-in node user (least privilege). The frontend
# container serves static assets + proxies the API and has no writable bind mounts,
# so non-root is safe here. (The backend container now also runs non-root; local
# docker-compose overrides `user: root` for its host-owned /data bind mount.)
RUN chown -R node:node /app
USER node
EXPOSE 3000
CMD ["node", "server.mjs"]
