"""Homework helper — guides students without giving direct answers."""
from __future__ import annotations

from query import query as retrieve
from llm import call_llm


def homework_help(question: str, course: str | None = None, top_k: int = 5) -> dict:
    """
    Find relevant material and guide the student with hints,
    without directly answering the question.
    Returns {guidance, sources}.
    """
    chunks = retrieve(question, course=course, top_k=top_k)

    if not chunks:
        return {
            "guidance": "I couldn't find relevant material for this question in your uploaded documents.",
            "sources": [],
        }

    sources_text = "\n\n".join(
        f"[{i}] {c['document_title']}, page {c['page_number']}\n{c['text']}"
        for i, c in enumerate(chunks, 1)
    )

    system = (
        "You are a study tutor helping a student work through a problem. "
        "Your goal is to guide the student to find the answer themselves — "
        "do NOT give the direct answer. "
        "Instead: point them to the relevant concepts, ask guiding questions, "
        "highlight which parts of the source material are most relevant, "
        "and give hints that lead them in the right direction. "
        "Base your guidance strictly on the provided source material."
    )

    prompt = (
        f"Source material:\n{sources_text}\n\n"
        f"Student's question: {question}\n\n"
        "Guide the student toward the answer without revealing it directly. "
        "Reference specific parts of the source material and ask follow-up questions "
        "that help them think it through."
    )

    guidance = call_llm(prompt, system=system)

    return {
        "guidance": guidance,
        "sources": chunks,
    }
