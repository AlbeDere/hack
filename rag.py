"""
RAG: retrieval-augmented generation.
Combines vector search (query.py) with LLM generation (llm.py).
"""

from query import query as retrieve
from llm import call_llm


def _format_sources(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['document_title']}, page {c['page_number']}\n{c['text']}"
        )
    return "\n\n".join(parts)


def rag(question: str, course: str = None, top_k: int = 5) -> dict:
    """
    Retrieve relevant chunks and generate an answer grounded in them.
    Returns {answer, sources}.
    """
    chunks = retrieve(question, course=course, top_k=top_k)

    if not chunks:
        return {
            "answer": "No relevant material found for this question.",
            "sources": [],
        }

    sources_text = _format_sources(chunks)

    system = (
        "You are a study assistant. Answer the student's question using ONLY "
        "the provided source excerpts. Always cite which source number(s) you "
        "used, e.g. [1], [2]. If the answer cannot be found in the sources, "
        "say so explicitly — do not guess or hallucinate."
    )

    prompt = (
        f"Sources:\n{sources_text}\n\n"
        f"Question: {question}"
    )

    answer = call_llm(prompt, system=system)

    return {
        "answer": answer,
        "sources": chunks,
    }


if __name__ == "__main__":
    import sys

    question = sys.argv[1] if len(sys.argv) > 1 else "What is the main topic?"
    course_filter = sys.argv[2] if len(sys.argv) > 2 else None

    result = rag(question, course=course_filter)

    print("\n=== Answer ===")
    print(result["answer"])
    print("\n=== Sources ===")
    for i, s in enumerate(result["sources"], 1):
        print(f"[{i}] {s['document_title']}, page {s['page_number']} (score: {s['score']:.3f})")
