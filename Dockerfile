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
# pip is part of the shipped image's attack surface (12 advisories on 25.0.1),
# so it is upgraded before the project install rather than left at the base tag.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir '.[server,mcp]' \
    && chown -R hoja:hoja /app
ENV HOJA_WEB_ROOT=/app/web
USER hoja
EXPOSE 8787
CMD ["sh", "-c", "hoja-serve migrate && hoja-serve serve --host 0.0.0.0 --port 8787"]
