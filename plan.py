"""Study plan generator based on weak concepts."""
from __future__ import annotations

import json

from llm import call_llm
from query import query as retrieve
from quiz import get_chunks_for_concept, generate_questions


def _get_reading_sources(concept: str, course: str | None) -> list[dict]:
    """Find top chunks for a concept and return as reading references."""
    chunks = get_chunks_for_concept(concept, course)
    if not chunks:
        chunks = retrieve(concept, course=course, top_k=3)
    seen = set()
    refs = []
    for c in chunks[:4]:
        key = (c.get("document_title", ""), c.get("page_number", ""))
        if key not in seen:
            seen.add(key)
            refs.append({
                "document": c.get("document_title", ""),
                "page": c.get("page_number", ""),
                "excerpt": c.get("text", "")[:120] + "...",
            })
    return refs


def _get_practice_questions(concept: str, course: str | None, n: int = 3) -> list[dict]:
    """Generate actual quiz questions for a concept."""
    chunks = get_chunks_for_concept(concept, course)
    if not chunks:
        chunks = retrieve(concept, course=course, top_k=3)
    if not chunks:
        return []
    questions = generate_questions(chunks, n=n, concept=concept)
    return [{"question": q.get("question", ""), "source_text": q.get("source_text", "")} for q in questions]


def _distribute_concepts(concepts: list[str], days: int) -> list[list[str]]:
    """Spread concepts across days, leaving last day for review."""
    study_days = max(1, days - 1)
    buckets: list[list[str]] = [[] for _ in range(study_days)]
    for i, concept in enumerate(concepts):
        buckets[i % study_days].append(concept)
    return buckets


def generate_plan(
    weak_concepts: list[str],
    days_until_exam: int,
    target_grade: str,
    course: str | None = None,
) -> dict:
    """
    Generate a grounded day-by-day study plan with real reading references
    and actual quiz questions from the uploaded material.
    Returns {plan: str, schedule: list[{day, date_label, concepts, reading, questions}]}
    """
    if not weak_concepts:
        return {
            "plan": "No weak concepts found — you're ready for the exam!",
            "schedule": [],
        }

    # LLM generates the overall strategy text
    concepts_list = "\n".join(f"- {c}" for c in weak_concepts)
    strategy_prompt = (
        f"Course: {course or 'General'}\n"
        f"Days until exam: {days_until_exam}\n"
        f"Target grade: {target_grade} (scale 1-5)\n"
        f"Weak concepts:\n{concepts_list}\n\n"
        "Write a brief 2-3 sentence overall study strategy for this student. "
        "Be concise and motivating."
    )
    plan_text = call_llm(strategy_prompt)

    # Build schedule grounded in real material
    buckets = _distribute_concepts(weak_concepts, days_until_exam)
    schedule = []

    for day_idx, day_concepts in enumerate(buckets, 1):
        day_entry: dict = {
            "day": day_idx,
            "date_label": f"Day {day_idx}",
            "concepts": day_concepts,
            "reading": [],
            "questions": [],
        }
        for concept in day_concepts:
            day_entry["reading"].extend(_get_reading_sources(concept, course))
            day_entry["questions"].extend(_get_practice_questions(concept, course, n=2))
        schedule.append(day_entry)

    # Last day = review day
    if days_until_exam > 1:
        schedule.append({
            "day": days_until_exam,
            "date_label": f"Day {days_until_exam} — Review",
            "concepts": weak_concepts,
            "reading": [],
            "questions": [],
            "note": "Review all concepts. Re-read excerpts you found difficult. Redo any questions you got wrong.",
        })

    return {"plan": plan_text, "schedule": schedule}
