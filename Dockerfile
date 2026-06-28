# ---- Stage 1: frontend build ----
FROM node:20-slim AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----
FROM python:3.12-slim
WORKDIR /workspace

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project

COPY backend/app backend/app/
RUN mkdir -p backend/data

COPY --from=frontend-builder /app/dist frontend/dist/

ENV PYTHONPATH=/workspace/backend
ENV STATIC_FILES_DIR=/workspace/frontend/dist
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "exec /workspace/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
