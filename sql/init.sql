CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  modified_time TIMESTAMPTZ NOT NULL,
  checksum TEXT NOT NULL,
  indexing_version TEXT NOT NULL DEFAULT 'tokenizer-aligned-v1',
  embedding_model TEXT NOT NULL DEFAULT '',
  embedding_tokenizer TEXT NOT NULL DEFAULT '',
  chunk_size INTEGER NOT NULL DEFAULT 500,
  chunk_overlap INTEGER NOT NULL DEFAULT 75,
  last_indexed_at TIMESTAMPTZ,
  missing_since TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
  id BIGSERIAL PRIMARY KEY,
  document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  section TEXT,
  page INTEGER,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_tsvector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  embedding VECTOR(1024),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);
CREATE INDEX IF NOT EXISTS idx_documents_missing_since ON documents(missing_since);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsvector ON chunks USING GIN(content_tsvector);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
