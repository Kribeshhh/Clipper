FROM ghcr.io/astral-sh/uv:0.13.8 AS uv
FROM python:3.13-slim-trixie AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libseccomp2 fonts-dejavu-core && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip && rm -rf /usr/local/lib/python3.13/ensurepip \
    && install -d -m 0700 -o 10001 -g 10001 /var/lib/clipper
WORKDIR /app
FROM base AS deps
COPY --from=uv /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
FROM deps AS dev
RUN uv sync --locked --no-install-project
COPY backend/ .
USER 10001:10001
CMD ["uvicorn","app.main:create_app","--factory","--host","0.0.0.0","--port","8000","--reload","--no-access-log"]
FROM base AS runtime
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
COPY --from=deps /app/.venv /app/.venv
COPY backend/app ./app
COPY backend/alembic.ini .
COPY backend/migrations ./migrations
USER 10001:10001
CMD ["uvicorn","app.main:create_app","--factory","--host","0.0.0.0","--port","8000","--no-access-log"]

FROM deps AS analysis-deps
RUN uv sync --locked --no-dev --group analysis --no-install-project
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-tiny',revision='d90ca5fe260221311c53c58e660288d3deb8d356',local_dir='/opt/whisper-tiny',allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.txt'])"
FROM runtime AS analysis
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=analysis-deps /app/.venv /app/.venv
COPY --from=analysis-deps /opt/whisper-tiny /opt/whisper-tiny
ENV HF_HUB_OFFLINE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER 10001:10001
