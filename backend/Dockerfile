# Pin the base image by digest (tag kept for readability) so builds are reproducible
# — an IVDR requirement — and don't drift if the upstream tag is repushed.
FROM python:3.10-slim@sha256:5f9928ea39771e8ddf4fb9a96ab24f65f087793635614405a1dc9384f040852e AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt .
# pyBigWig publishes manylinux wheels for x86_64 only, so on arm64 (Apple Silicon
# development machines) pip falls back to its sdist and needs a C toolchain and
# libcurl headers. Deploy targets are linux/amd64 and take the wheel, but the build
# has to work on both.
#
# The toolchain is installed and purged inside one RUN so it never reaches a layer
# of the shipped image: the runtime stays compiler-free, which is both a smaller
# image and one fewer thing in the attack surface (TF-13). The hash-locked
# requirements still govern what gets built -- pip verifies the sdist against the
# same lock as every wheel.
RUN set -eux; \
    build_deps='gcc libc6-dev libcurl4-openssl-dev zlib1g-dev'; \
    apt-get update; \
    # libcurl4 is the *runtime* library the built extension links against, and is
    # installed separately so `--auto-remove` below does not take it away with the
    # -dev package that pulled it in.
    apt-get install -y --no-install-recommends libcurl4 $build_deps; \
    pip install --no-cache-dir -r requirements.txt; \
    apt-get purge -y --auto-remove $build_deps; \
    apt-get install -y --no-install-recommends libcurl4; \
    rm -rf /var/lib/apt/lists/*

COPY backend/db ./db
COPY backend/app ./app
# Ship the clinical CNV knowledgebase build script so the admin "rebuild
# knowledgebase" action can invoke it (see CLINICAL_CNV_KB_SCRIPT_PATH).
COPY scripts ./scripts

# Build identity, injected via --build-arg (see docker-compose build args). Placed
# after the dependency layers so changing the SHA doesn't bust the pip-install cache.
# Baked into the base image as ENV so both dev and production runtimes report it via
# Settings (the .git dir is excluded from the build context, so the short SHA must be
# passed in by the build invoker, e.g. GIT_SHA="$(git rev-parse --short=12 HEAD)").
ARG APP_VERSION=0.0.0+unknown
ARG GIT_SHA=unknown
ENV APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA}

EXPOSE 8000

# `--proxy-headers` makes uvicorn honour X-Forwarded-For/Proto from a trusted
# front end so `request.client.host` is the real client IP (needed for the signup
# rate-limit bucket and audit logging — behind Cloud Run / a GCP HTTPS LB the raw
# peer is a Google-internal IP). uvicorn only trusts forwarded headers from peers
# in FORWARDED_ALLOW_IPS (default 127.0.0.1); set that env to the trusted proxy
# range in the runtime (see terraform/cloudrun.tf FORWARDED_ALLOW_IPS).
FROM base AS dev
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--reload"]

FROM base AS production
# Drop root: create an unprivileged user and give it the app tree + the /data mount
# point. In Cloud Run /data/ref-data is a read-only gcsfuse mount (world-readable, so
# a non-root UID can read it) and PHI family data goes to GCS via the API — not this
# path. Local docker-compose bind-mounts a host-owned ./data and overrides
# `user: root` to keep that writable; the hardening targets the deployed image default.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
