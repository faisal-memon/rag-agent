FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1


FROM base AS api

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY app/__init__.py ./app/__init__.py
COPY app/api ./app/api
COPY app/core ./app/core
COPY sql ./sql

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS embed

ARG EMBEDDING_TOKENIZER_MODEL_ID=mixedbread-ai/mxbai-embed-large-v1
ARG HF_TOKEN=""

ENV HF_HOME=/opt/huggingface \
    EMBEDDING_TOKENIZER_MODEL_ID=${EMBEDDING_TOKENIZER_MODEL_ID} \
    EMBEDDING_TOKENIZER_LOCAL_FILES_ONLY=true

COPY requirements-embed.txt .
RUN pip install --no-cache-dir -r requirements-embed.txt
RUN HF_TOKEN=${HF_TOKEN} python -c "import os; from transformers import AutoTokenizer; AutoTokenizer.from_pretrained(os.environ['EMBEDDING_TOKENIZER_MODEL_ID'], token=(os.environ.get('HF_TOKEN') or None)); print(f\"cached tokenizer {os.environ['EMBEDDING_TOKENIZER_MODEL_ID']}\")"
COPY app/__init__.py ./app/__init__.py
COPY app/core ./app/core
COPY app/embed ./app/embed
COPY sql ./sql

CMD ["python", "-m", "app.embed.main"]


FROM base AS normalize

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-normalize.txt .
RUN pip install --no-cache-dir -r requirements-normalize.txt
COPY app/__init__.py ./app/__init__.py
COPY app/core ./app/core
COPY app/normalize ./app/normalize

CMD ["python", "-m", "app.normalize.main"]
