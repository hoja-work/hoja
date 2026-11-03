FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN groupadd --system cliniar && useradd --system --gid cliniar --home /app --shell /usr/sbin/nologin cliniar
WORKDIR /app
COPY . .
COPY --from=frontend-build /frontend/dist /app/web
# pip is part of the shipped image's attack surface (12 advisories on 25.0.1),
# so it is upgraded before the project install rather than left at the base tag.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir '.[server,mcp]' \
    && chown -R cliniar:cliniar /app
ENV CLINIAR_WEB_ROOT=/app/web
USER cliniar
EXPOSE 8787
CMD ["sh", "-c", "cliniar-serve migrate && cliniar-serve serve --host 0.0.0.0 --port 8787"]
