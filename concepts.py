"""
Concept extraction and management.

Two-phase approach:
  Phase 1 (once per document): feed full text to LLM → get 5-15 master concepts → store in DB
  Phase 2 (per chunk): assign from existing master list only — no new concepts created
"""

import json
import struct
import math
from db.connection import get_connection
from llm import call_llm

MAX_CONCEPTS_PER_CHUNK = 3
MAX_DOCUMENT_CONCEPTS = 15
MIN_DOCUMENT_CONCEPTS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_existing_concepts(course: str) -> list[dict]:
    """Return all concepts for this course: {id, name, embedding}."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, embedding FROM concepts WHERE course = ?",
            (course,),
        )
        rows = []
        for row in cur.fetchall():
            rows.append({
                "id": row["id"],
                "name": row["name"],
                "embedding": _blob_to_vec(row["embedding"]) if row["embedding"] else None,
            })
        return rows
    finally:
        conn.close()


def _insert_concept(name: str, course: str, embedding: list[float]) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO concepts (name, course, embedding) VALUES (?, ?, ?)",
            (name, course, _vec_to_blob(embedding)),
        )
        conn.commit()
        cur.execute("SELECT id FROM concepts WHERE name = ?", (name,))
        return cur.fetchone()["id"]
    finally:
        conn.close()


def _link_chunk_concept(chunk_id: int, concept_id: int):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO chunk_concepts (chunk_id, concept_id) VALUES (?, ?)",
            (chunk_id, concept_id),
        )
        conn.commit()
    finally:
        conn.close()


def _link_document_concept(document_id: int, concept_id: int):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO document_concepts (document_id, concept_id) VALUES (?, ?)",
            (document_id, concept_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 1: extract master concept list from full document text
# ---------------------------------------------------------------------------

def extract_document_concepts(full_text: str, course: str, embed_fn, document_id: int) -> list[str]:
    """
    Send the full document text to LLM once and extract a bounded master concept list.
    Inserts concepts into the DB and returns their names.
    """
    # Trim to avoid token limits (~12k chars ≈ ~3k tokens, safe for most models)
    trimmed = full_text[:12000]

    system = (
        "You are an academic concept extractor for a study tool. "
        "Identify the key concepts covered in this document. "
        "Respond ONLY with a JSON array of short concept names (1-3 words each), lowercase. "
        "No explanation, no markdown — only the JSON array."
    )

    prompt = (
        f"Document text:\n{trimmed}\n\n"
        f"Extract {MIN_DOCUMENT_CONCEPTS}-{MAX_DOCUMENT_CONCEPTS} key concepts that cover "
        f"the main topics of this document. Be broad — these will be used to categorise "
        f"all sections of the document. Use the same language as the document."
    )

    response = call_llm(prompt, system=system)

    try:
        clean = response.strip().strip("```json").strip("```").strip()
        names = json.loads(clean)
        if isinstance(names, list):
            names = [str(n).lower().strip() for n in names if n][:MAX_DOCUMENT_CONCEPTS]
    except (json.JSONDecodeError, ValueError):
        names = []

    if not names:
        print("  Warning: LLM returned no concepts for document.")
        return []

    print(f"  Master concepts: {names}")

    # Store in DB and link to document
    for name in names:
        embedding = embed_fn(name)
        concept_id = _insert_concept(name, course, embedding)
        _link_document_concept(document_id, concept_id)

    return names


# ---------------------------------------------------------------------------
# Phase 2: assign chunks to existing concepts only
# ---------------------------------------------------------------------------

def _assign_via_llm(chunk_text: str, existing_names: list[str]) -> list[str]:
    existing_str = ", ".join(f'"{n}"' for n in existing_names)

    system = (
        "You are a concept tagging assistant for a study tool. "
        "Respond ONLY with a JSON array of strings from the provided list. "
        "No explanation, no new concepts, no markdown."
    )

    prompt = (
        f"Available concepts: [{existing_str}]\n\n"
        f"Text:\n{chunk_text}\n\n"
        f"Select 1-{MAX_CONCEPTS_PER_CHUNK} concepts from the list above that best describe "
        f"what this text is about. Only use concepts from the provided list."
    )

    response = call_llm(prompt, system=system)

    try:
        clean = response.strip().strip("```json").strip("```").strip()
        chosen = json.loads(clean)
        if isinstance(chosen, list):
            # Validate — only accept names that are in the existing list
            valid = [str(c).lower().strip() for c in chosen if str(c).lower().strip() in existing_names]
            return valid[:MAX_CONCEPTS_PER_CHUNK]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def assign_concepts(chunk_id: int, chunk_text: str, course: str, embed_fn=None) -> list[str]:
    """
    Assign existing concepts to a chunk. Never creates new concepts.
    Returns list of assigned concept names.
    """
    existing = _get_existing_concepts(course)
    if not existing:
        return []

    existing_names = [c["name"] for c in existing]
    chosen_names = _assign_via_llm(chunk_text, existing_names)

    for name in chosen_names:
        concept = next((c for c in existing if c["name"] == name), None)
        if concept:
            _link_chunk_concept(chunk_id, concept["id"])

    return chosen_names
