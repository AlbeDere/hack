"""
RAG: retrieval-augmented generation.
Combines vector search (query.py) with LLM generation (llm.py).
"""

from query import query as retrieve
from llm import call_llm, call_llm_stream


def _format_sources(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['document_title']}, page {c['page_number']}\n{c['text']}"
        )
    return "\n\n".join(parts)


def rag(question: str, course: str = None, top_k: int = 5, history: list = None) -> dict:
    """
    Retrieve relevant chunks and generate an answer grounded in them.
    Optionally accepts conversation history for multi-turn context.
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
        "You are a study assistant in an ongoing conversation with a student. "
        "Answer using ONLY the provided source excerpts. "
        "Keep context from the conversation history. "
        "Cite sources with [1], [2] etc. "
        "If the answer cannot be found in the sources, say so — do not guess."
    )

    history_text = ""
    if history:
        for msg in history[-6:]:  # last 3 exchanges
            role = "Student" if msg.get("role") == "user" else "Assistant"
            history_text += f"{role}: {msg.get('content', '')}\n"

    prompt = (
        f"Sources:\n{sources_text}\n\n"
        + (f"Conversation so far:\n{history_text}\n" if history_text else "")
        + f"Student: {question}"
    )

    answer = call_llm(prompt, system=system)

    return {
        "answer": answer,
        "sources": chunks,
    }


def rag_stream(question: str, course: str = None, top_k: int = 5):
    """
    Same as rag() but streams the answer token by token.
    Yields string tokens. Sources are not returned (use /rag for those).
    """
    chunks = retrieve(question, course=course, top_k=top_k)

    if not chunks:
        yield "No relevant material found for this question."
        return

    sources_text = _format_sources(chunks)

    system = (
        "You are a study assistant. Answer the student's question using ONLY "
        "the provided source excerpts. Always cite which source number(s) you "
        "used, e.g. [1], [2]. If the answer cannot be found in the sources, "
        "say so explicitly — do not guess or hallucinate."
    )

    prompt = f"Sources:\n{sources_text}\n\nQuestion: {question}"

    yield from call_llm_stream(prompt, system=system)


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
