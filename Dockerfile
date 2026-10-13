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
RUN pip install --no-cache-dir ./connectors/google_workspace '.[server,mcp,agent]' \
    && chown -R cliniar:cliniar /app
RUN mkdir -p /app/data/agent-uploads && chown -R cliniar:cliniar /app/data
ENV CLINIAR_WEB_ROOT=/app/web
USER cliniar
EXPOSE 8787
CMD ["sh", "-c", "cliniar-serve migrate && cliniar-serve serve --host 0.0.0.0 --port 8787"]
