"""
Hybrid search: vector similarity (sqlite-vec) + keyword (FTS5),
merged with Reciprocal Rank Fusion (RRF).
"""

import os
import struct
import httpx
from dotenv import load_dotenv
from db.connection import get_connection

load_dotenv()

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15")

RRF_K = 60  # standard RRF constant


def embed_query(text: str) -> list[float]:
    url = (
        f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{AZURE_DEPLOYMENT}/embeddings?api-version={AZURE_API_VERSION}"
    )
    response = httpx.post(
        url,
        headers={"Content-Type": "application/json", "api-key": AZURE_API_KEY},
        json={"input": [text]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def _vector_search(blob: bytes, course: str | None, fetch: int) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        if course:
            cur.execute(
                """
                SELECT c.id, c.text, c.page_number, d.title AS document_title, d.course
                FROM chunk_embeddings ce
                JOIN chunks c ON c.id = ce.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE ce.embedding MATCH ? AND k = ?
                  AND d.course = ?
                ORDER BY ce.distance
                """,
                (blob, fetch, course),
            )
        else:
            cur.execute(
                """
                SELECT c.id, c.text, c.page_number, d.title AS document_title, d.course
                FROM chunk_embeddings ce
                JOIN chunks c ON c.id = ce.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE ce.embedding MATCH ? AND k = ?
                ORDER BY ce.distance
                """,
                (blob, fetch),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _fts_search(question: str, course: str | None, fetch: int) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        if course:
            cur.execute(
                """
                SELECT c.id, c.text, c.page_number, d.title AS document_title, d.course
                FROM chunks_fts f
                JOIN chunks c ON c.id = f.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE chunks_fts MATCH ? AND d.course = ?
                ORDER BY rank
                LIMIT ?
                """,
                (question, course, fetch),
            )
        else:
            cur.execute(
                """
                SELECT c.id, c.text, c.page_number, d.title AS document_title, d.course
                FROM chunks_fts f
                JOIN chunks c ON c.id = f.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (question, fetch),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _rrf(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion."""
    scores: dict[int, float] = {}
    rows: dict[int, dict] = {}

    for ranked in ranked_lists:
        for rank, row in enumerate(ranked):
            chunk_id = row["id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
            rows[chunk_id] = row

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result = []
    for chunk_id, score in merged:
        entry = dict(rows[chunk_id])
        entry["score"] = score
        result.append(entry)
    return result


def query(question: str, course: str = None, top_k: int = 5) -> list[dict]:
    """
    Hybrid search: vector + FTS5, merged with RRF.
    Returns top_k results with {id, text, page_number, document_title, course, score}.
    """
    fetch = top_k * 4  # over-fetch before merging

    embedding = embed_query(question)
    blob = struct.pack(f"{len(embedding)}f", *embedding)

    vector_results = _vector_search(blob, course, fetch)

    try:
        fts_results = _fts_search(question, course, fetch)
    except Exception:
        # FTS may fail on tokens it can't parse; fall back to vector only
        fts_results = []

    return _rrf([vector_results, fts_results])[:top_k]


if __name__ == "__main__":
    import sys

    question = sys.argv[1] if len(sys.argv) > 1 else "What is the main topic?"
    course_filter = sys.argv[2] if len(sys.argv) > 2 else None

    results = query(question, course=course_filter)
    for i, r in enumerate(results, 1):
        print(f"\n--- Result {i} (rrf score: {r['score']:.4f}) ---")
        print(f"Source: {r['document_title']}, page {r['page_number']}, course: {r['course']}")
        print(r["text"])


import os
import httpx
from dotenv import load_dotenv
from db.connection import get_connection

load_dotenv()

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15")


def embed_query(text: str) -> list[float]:
    url = (
        f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{AZURE_DEPLOYMENT}/embeddings?api-version={AZURE_API_VERSION}"
    )
    response = httpx.post(
        url,
        headers={"Content-Type": "application/json", "api-key": AZURE_API_KEY},
        json={"input": [text]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def query(question: str, course: str = None, top_k: int = 5) -> list[dict]:
    """
    Find the top_k most relevant chunks for a natural language question.
    Optionally filter by course name.
    Returns list of dicts: {text, page_number, document_title, course, score}
    """
    import struct

    embedding = embed_query(question)
    blob = struct.pack(f"{len(embedding)}f", *embedding)

    conn = get_connection()
    try:
        cur = conn.cursor()
        if course:
            cur.execute(
                """
                SELECT
                    c.text,
                    c.page_number,
                    d.title  AS document_title,
                    d.course,
                    ce.distance AS score
                FROM chunk_embeddings ce
                JOIN chunks c ON c.id = ce.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE ce.embedding MATCH ? AND k = ?
                  AND d.course = ?
                ORDER BY ce.distance
                """,
                (blob, top_k * 4, course),  # over-fetch then filter
            )
        else:
            cur.execute(
                """
                SELECT
                    c.text,
                    c.page_number,
                    d.title  AS document_title,
                    d.course,
                    ce.distance AS score
                FROM chunk_embeddings ce
                JOIN chunks c ON c.id = ce.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE ce.embedding MATCH ? AND k = ?
                ORDER BY ce.distance
                """,
                (blob, top_k),
            )
        rows = cur.fetchmany(top_k)
        return [dict(row) for row in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    import json

    question = sys.argv[1] if len(sys.argv) > 1 else "What is the main topic?"
    course_filter = sys.argv[2] if len(sys.argv) > 2 else None

    results = query(question, course=course_filter)
    for i, r in enumerate(results, 1):
        print(f"\n--- Result {i} (score: {r['score']:.3f}) ---")
        print(f"Source: {r['document_title']}, page {r['page_number']}, course: {r['course']}")
        print(r["text"])
