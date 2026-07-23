# RAG Agent

RAG Agent is a local-first MVP for a headless document agent that ingests files from a mounted directory, stores normalized chunks in PostgreSQL with `pgvector`, and exposes FastAPI endpoints plus a small web console for cited question answering.

## MVP scope

- Local filesystem ingestion from a mounted document directory, including Nextcloud-backed folders
- Text extraction for `PDF`, `DOCX`, `TXT`, and `Markdown`
- PostgreSQL full-text search plus semantic search with `pgvector`
- Agent-selected keyword or semantic retrieval with cited answers through OpenAI or a local `llama.cpp`
  OpenAI-compatible endpoint
- Separate configuration for chat and embedding providers, including configurable embedding prefixes
- OCR-aware parsing for image receipts and scanned PDFs, with configurable thresholds
- Optional Docling-backed normalization phase that writes Markdown plus JSON metadata sidecars before database indexing
- Long-running normalization watcher for incremental raw-document changes
- Long-running embed watcher for incremental normalized Markdown indexing
- `POST /reindex`, `POST /agent/query`, and debug retrieval endpoints

## Not yet implemented

- Advanced schedule or receipt metadata extraction
- Reranking and date-aware retrieval

## Quick start

1. Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`.
   If you want local answer generation, set `LLM_PROVIDER=llamacpp` and point `LLAMACPP_BASE_URL` at your `llama.cpp` server.
   If you want local embeddings, set `EMBEDDING_PROVIDER=llamacpp` and point `LLAMACPP_EMBEDDING_BASE_URL` at your embedding server.
   Use `EMBEDDING_QUERY_PREFIX` and `EMBEDDING_DOCUMENT_PREFIX` to match your embedding model's expected prompt format.
   The container image prefetches the embedding tokenizer at build time and defaults `EMBEDDING_TOKENIZER_LOCAL_FILES_ONLY=true` so runtime chunking does not depend on network access.
   Use `RAG_ENABLED_SUFFIXES` to control which raw file types the normalizer watches.
   Docling handles OCR for image files and scanned PDFs in the normalizer image.
2. Put source files under `./data/nextcloud` or mount your Nextcloud storage there.
3. Start the stack:

```bash
docker compose up --build
```

4. Reindex documents:

```bash
curl -X POST http://localhost:8000/reindex
```

Or run the reconciliation worker directly:

```bash
python -m app.embed.worker
```

You can also run the first normalization phase directly. It writes Markdown artifacts under `NORMALIZED_OUTPUT_DIR`.
By default `NORMALIZATION_BACKEND=docling`. Structured documents such as PDFs, DOCX files, and images are normalized through Docling; `.txt` and `.md` files use a lightweight passthrough.

```bash
python -m app.normalize.worker
```

The normalizer image runs as a long-lived watcher by default. For local development, you can run the watcher directly:

```bash
python -m app.normalize.main
```

The embed image runs as a long-lived watcher by default. It watches normalized Markdown and metadata artifacts, then indexes only the changed normalized documents:

```bash
python -m app.embed.main
```

For a one-shot manual reindex, run:

```bash
python -m app.embed.worker
```

5. Ask a question through the agent. It iteratively chooses read-only tools, inspects each result, and decides whether
to investigate further before answering:

```bash
curl -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What is my latest glasses prescription?"}'
```

The agent can search indexed passages and document metadata, grep normalized Markdown, and read bounded line ranges from
candidate documents. Each request is limited by `RAG_AGENT_MAX_STEPS`, which defaults to six tool calls. The web console presents replies as a conversation,
keeps recent history in browser local storage, and sends recent user/assistant turns with follow-up requests. Thinking,
tool calls, tool results, and controller decisions are shown as a folded chronological trace; saved reasoning is not sent
back to the model as conversation context. See [the agent runtime walkthrough](docs/agent-runtime.md) for the controller,
protocol, and tool boundaries.

Web routes:

- `/` is the agent-only conversation.
- `/debug` contains retrieval modes, raw retrieval debugging, pipeline status, and agent inspection.
- `/docs` contains the generated FastAPI API documentation.

## Layout

- `app/api/`: FastAPI entrypoint, search tools, page rendering, static web assets, templates, and API schemas
- `app/normalize/`: Phase 1 raw document normalization to Markdown and JSON metadata, using Docling by default
- `app/embed/`: normalized Markdown watching/scanning, mismatch reconciliation, chunking, embedding, and DB upserts
- `app/core/`: shared settings, DB, parser, tokenizer/chunking, and model client helpers
- `app/api/config.py`, `app/embed/config.py`, `app/normalize/config.py`: runtime-specific settings grouped with
  the code that uses them
- `sql/init.sql`: schema and indexes
- `Dockerfile`: multi-target runtime image for `api`, `embed`, and `normalize`

## Container targets

The Dockerfile has three role-specific targets so the API does not need to carry the heavy Docling/OCR runtime:

- `api`: FastAPI query/debug service, retrieval, database access, and LLM clients
- `embed`: normalized Markdown indexing daemon, chunking, embedding, and database upserts
- `normalize`: Docling/OCR raw-document normalization watcher, plus the batch worker for manual backfills

Build them locally with:

```bash
export GHCR_OWNER=<owner>
make docker-build-api
make docker-build-embed
make docker-build-normalize
```

Release builds publish separate GHCR packages:

```text
ghcr.io/<owner>/rag-agent-api
ghcr.io/<owner>/rag-agent-embed
ghcr.io/<owner>/rag-agent-normalize
```

`RAG_ENABLED_SUFFIXES` is the normalizer allowlist. It is intersected with the formats the code knows how to normalize today: `.pdf`, `.docx`, `.txt`, `.md`, `.jpg`, `.jpeg`, and `.png`.
