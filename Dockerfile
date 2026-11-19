FROM node:26-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN groupadd --system hoja && useradd --system --gid hoja --home /app --shell /usr/sbin/nologin hoja
WORKDIR /app
COPY . .
COPY --from=frontend-build /frontend/dist /app/web
# The customer-side daemon's wheel, if the release placed one here (HOJA-712). The
# build context is this directory — `COPY frontend/` above depends on that — so the
# sibling `runner/` tree is not reachable from a Dockerfile stage, and the wheel has to
# exist before the build: `scripts/build-runner-wheel.sh` writes it into ./downloads, and
# this `COPY . .` carries it to /app/downloads, which GET /downloads reads. Absent (a
# plain `docker compose build` from a checkout), the app still serves; the enrollment
# screen then says the wheel is unpublished instead of printing a URL that would 404.
# Same build identity as the production image: GET /api/version reports the
# commit this image was built from, and when (never a hardcoded version).
ARG HOJA_RELEASE_SHA=unknown
ARG HOJA_BUILT_AT=
ENV HOJA_RELEASE_SHA=$HOJA_RELEASE_SHA
RUN if [ -n "$HOJA_BUILT_AT" ]; then printf '%s\n' "$HOJA_BUILT_AT" > /app/BUILD_TIME; \
    else date -u +%Y-%m-%dT%H:%M:%SZ > /app/BUILD_TIME; fi
# pip is part of the shipped image's attack surface (12 advisories on 25.0.1),
# so it is upgraded before the project install rather than left at the base tag.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir '.[server]' \
    && chown -R hoja:hoja /app
ENV HOJA_WEB_ROOT=/app/web
USER hoja
EXPOSE 8787
# exec, so hoja-serve *is* PID 1: the shell that runs migrate is replaced by
# the server, and the runtime's SIGTERM reaches the process that handles it
# (HOJA-234).
CMD ["sh", "-c", "hoja-serve migrate && exec hoja-serve serve --host 0.0.0.0 --port 8787"]
