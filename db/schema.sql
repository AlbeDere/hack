-- Run once via: python db/init_db.py
-- sqlite-vec handles vector storage; schema is plain SQLite
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    course TEXT,
    source_file TEXT,
    uploaded_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    page_number INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
-- sqlite-vec virtual table for 1536-dim embeddings (ada-002)
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float [1536]
);
-- FTS5 for keyword search (hybrid)
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content = chunks,
    content_rowid = id
);
-- Concepts extracted by LLM at ingest time
CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    course TEXT,
    embedding BLOB
);
-- Many-to-many: chunks <-> concepts
CREATE TABLE IF NOT EXISTS chunk_concepts (
    chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
    concept_id INTEGER REFERENCES concepts(id) ON DELETE CASCADE,
    PRIMARY KEY (chunk_id, concept_id)
);
-- Many-to-many: documents <-> concepts (document-level categorisation)
CREATE TABLE IF NOT EXISTS document_concepts (
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    concept_id INTEGER REFERENCES concepts(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, concept_id)
);
-- Quiz results per concept (best score tracked)
CREATE TABLE IF NOT EXISTS quiz_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept TEXT NOT NULL,
    course TEXT,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    percentage REAL NOT NULL,
    best INTEGER NOT NULL DEFAULT 0,
    -- 1 if this is the personal best
    taken_at TEXT DEFAULT (datetime('now'))
);
-- SM-2 spaced repetition state per concept
CREATE TABLE IF NOT EXISTS sm2_state (
    concept TEXT NOT NULL,
    course TEXT NOT NULL,
    easiness REAL NOT NULL DEFAULT 2.5,
    interval INTEGER NOT NULL DEFAULT 1,
    repetitions INTEGER NOT NULL DEFAULT 0,
    next_review TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (concept, course)
);