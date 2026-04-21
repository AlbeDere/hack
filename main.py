"""
Easels API — FastAPI wrapper around the RAG study companion.

Endpoints:
  POST /ingest              — upload a PDF and ingest it
  GET  /documents           — list all ingested documents
  GET  /documents/{id}/file — download original PDF
  GET  /concepts            — list all concepts, optionally filtered by course
  GET  /courses             — list all courses
  POST /rag                 — RAG query: retrieve + generate grounded answer
  POST /quiz/generate       — generate quiz questions for a concept
  POST /quiz/evaluate       — evaluate a single student answer
  POST /quiz/result         — save a completed quiz result
  GET  /quiz/weak           — get weakest concepts by quiz history
"""

import json
import os
import re
import shutil
import tempfile

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ingest import ingest
from rag import rag, rag_stream
from summary import build_summary
from speech import synthesize
from plan import generate_plan
from homework_helper import homework_help
from sm2 import update_sm2, get_next_concept
from quiz import list_concepts as _list_concepts
from quiz import (
    get_chunks_for_concept,
    generate_questions,
    evaluate_answer,
    save_quiz_result,
    get_weak_concepts,
    get_progress,
)
from db.connection import get_connection

app = FastAPI(title="Easels API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RAGRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "question": "Mis on nimipinge ja milleks seda kasutatakse?",
        "course": "Elektrisüsteem",
        "history": [
            {"role": "user", "content": "Mis on alalisvool?"},
            {"role": "assistant", "content": "Alalisvool on vool, mis voolab alati samas suunas..."}
        ],
        "top_k": 5
    }}}

    question: str
    course: str | None = None
    top_k: int = 5
    history: list[dict] = []  # [{"role": "user"|"assistant", "content": "..."}]


class RAGResponse(BaseModel):
    answer: str
    sources: list[dict]


class QuizGenerateRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [
        {
            "summary": "Single concept",
            "value": {"concept": "nimipinged", "course": "Elektrisüsteem", "n_questions": 3}
        },
        {
            "summary": "Multiple concepts",
            "value": {"concepts": ["nimipinged", "alalisvool"], "course": "Elektrisüsteem", "n_questions": 6}
        }
    ]}}

    concept: str | None = None          # single concept (legacy)
    concepts: list[str] = []            # multiple concepts
    course: str | None = None
    n_questions: int = 5


class QuizEvaluateRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "question": "Millised nimipinged vastavad IEC standarditele Eesti kõrgepingevõrkudes?",
        "student_answer": "10, 20, 35, 110 ja 220 kV",
        "source_text": "Eesti kõrgepingevõrkudes vastavad IEC-standarditele nimipinged 10, 20, 35, 110 ja 220 kV..."
    }}}

    question: str
    student_answer: str
    source_text: str


class QuizResultRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "concept": "nimipinged",
        "course": "Elektrisüsteem",
        "score": 2,
        "total": 3,
        "quality": 4
    }}}

    concept: str
    course: str | None = None
    score: int
    total: int
    quality: int | None = None  # 0-5 SM-2 quality; if provided, SM-2 is updated in same call


class PlanRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "course": "Elektrisüsteem",
        "days_until_exam": 5,
        "target_grade": "4",
        "limit_weak": 10
    }}}

    course: str | None = None
    days_until_exam: int
    target_grade: str = "5"
    limit_weak: int = 10


class HomeworkRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "question": "Miks kasutatakse alalisvooluliine pikkade kaabelliinide korral?",
        "course": "Elektrisüsteem",
    }}}

    question: str
    course: str | None = None
    top_k: int = 5


class SM2UpdateRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "concept": "nimipinged",
        "course": "Elektrisüsteem",
        "quality": 4
    }}}

    concept: str
    course: str
    quality: int  # 0-5


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@app.post("/ingest",
    summary="Upload and ingest a PDF",
    description="Upload a PDF file to extract text, embed chunks, and extract concepts. "
               "The document title and course will appear in `/documents` and concept source lookups."
)
async def ingest_pdf(
    file: UploadFile = File(...),
    title: str = Form(...),
    course: str = Form(...),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        ingest(tmp_path, title, course)
    finally:
        os.unlink(tmp_path)

    return {"status": "ok", "title": title, "course": course}


# ---------------------------------------------------------------------------
# Concepts & Courses
# ---------------------------------------------------------------------------

@app.get("/documents",
    summary="List all ingested documents",
    description="Returns all uploaded documents with their title, course, and upload date."
)
def list_documents(course: str | None = None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        if course:
            cur.execute(
                "SELECT id, title, course, uploaded_at FROM documents WHERE lower(course)=lower(?) ORDER BY uploaded_at DESC",
                (course,),
            )
        else:
            cur.execute("SELECT id, title, course, uploaded_at FROM documents ORDER BY uploaded_at DESC")
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"documents": [
        {"id": r["id"], "title": r["title"], "course": r["course"], "uploaded_at": r["uploaded_at"]}
        for r in rows
    ]}


@app.get("/concepts/{concept}/sources",
    summary="Get source documents for a concept",
    description="Returns all documents and specific pages that contain the given concept. "
               "Use `file_url` to link directly to the PDF. "
               "Pages are the exact page numbers where the concept appears in the document."
)
def get_concept_sources(concept: str, course: str | None = None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Find concept id (case-insensitive)
        cur.execute("SELECT id FROM concepts WHERE lower(name)=lower(?)", (concept,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Concept '{concept}' not found")
        concept_id = row["id"]

        # Get distinct documents + pages via chunk_concepts join
        if course:
            cur.execute("""
                SELECT DISTINCT d.id, d.title, d.course, c.page_number
                FROM chunk_concepts cc
                JOIN chunks c ON c.id = cc.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE cc.concept_id = ?
                  AND lower(d.course) = lower(?)
                ORDER BY d.title, c.page_number
            """, (concept_id, course))
        else:
            cur.execute("""
                SELECT DISTINCT d.id, d.title, d.course, c.page_number
                FROM chunk_concepts cc
                JOIN chunks c ON c.id = cc.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE cc.concept_id = ?
                ORDER BY d.title, c.page_number
            """, (concept_id,))
        rows = cur.fetchall()
    finally:
        conn.close()

    # Group pages per document
    docs: dict[int, dict] = {}
    for r in rows:
        did = r["id"]
        if did not in docs:
            docs[did] = {
                "id": did,
                "title": r["title"],
                "course": r["course"],
                "pages": [],
            }
        if r["page_number"] is not None:
            docs[did]["pages"].append(r["page_number"])

    return {"concept": concept, "sources": list(docs.values())}


@app.get("/concepts", summary="List all concepts")
def get_concepts(course: str | None = None):
    return {"concepts": _list_concepts(course)}


@app.get("/courses", summary="List all courses")
def get_courses():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT course FROM documents WHERE course IS NOT NULL ORDER BY course")
        return {"courses": [r["course"] for r in cur.fetchall()]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------

@app.post("/rag", response_model=RAGResponse,
    summary="Ask a question",
    description="Retrieve relevant chunks from uploaded material and generate a grounded answer. "
               "Pass `history` (array of `{role, content}`) for multi-turn chat context. "
               "Returns `{answer: str, sources: list}`."
)
def ask(req: RAGRequest):
    result = rag(req.question, course=req.course, top_k=req.top_k, history=req.history)
    return result


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------

@app.post("/quiz/generate", summary="Generate quiz questions for one or more concepts")
def quiz_generate(req: QuizGenerateRequest):
    # Build concept list — support both single and multiple
    concept_list = req.concepts or (([req.concept]) if req.concept else [])
    if not concept_list:
        raise HTTPException(status_code=400, detail="Provide 'concept' or 'concepts'.")

    # Fetch and merge chunks for all concepts
    seen_ids = set()
    chunks = []
    for concept in concept_list:
        for c in get_chunks_for_concept(concept, req.course):
            if c["chunk_id"] not in seen_ids:
                seen_ids.add(c["chunk_id"])
                chunks.append(c)

    if not chunks:
        raise HTTPException(
            status_code=404,
            detail=f"No material found for concepts: {', '.join(concept_list)}.",
        )

    label = ", ".join(concept_list)
    questions = generate_questions(chunks, n=req.n_questions, concept=label)
    if not questions:
        raise HTTPException(status_code=500, detail="Could not generate questions.")
    return {"questions": questions}


@app.post("/quiz/evaluate",
    summary="Evaluate a student answer",
    description="Grade a single student answer against the source text. "
               "Pass the `source_text` returned by `/quiz/generate`. "
               "Returns `{correct: bool, score: int, feedback: str}`."
)
def quiz_evaluate(req: QuizEvaluateRequest):
    result = evaluate_answer(req.question, req.student_answer, req.source_text)
    return result


@app.post("/quiz/result", summary="Save quiz result (optionally updates SM-2 in same call)")
def quiz_result(req: QuizResultRequest):
    result = save_quiz_result(req.concept, req.course or "", req.score, req.total)
    if result is None:
        raise HTTPException(status_code=400, detail="total must be > 0")
    pct, is_best = result
    response: dict = {"percentage": pct, "is_personal_best": bool(is_best)}

    # Optionally update SM-2 in the same call
    if req.quality is not None:
        if not 0 <= req.quality <= 5:
            raise HTTPException(status_code=400, detail="quality must be 0-5")
        sm2_state = update_sm2(req.concept, req.course or "", req.quality)
        response["sm2"] = sm2_state

    return response


@app.get("/quiz/weak", summary="Get weakest concepts by quiz history")
def quiz_weak(course: str | None = None, limit: int = 5):
    return {"weak_concepts": get_weak_concepts(course, limit)}


@app.get("/progress",
    summary="Stats dashboard",
    description="Returns overall study progress: concepts attempted vs total, mastery rate (\u226580%), "
               "average score, daily streak, and per-concept SM-2 review schedule. "
               "Filter by `course` or omit for all courses."
)
def progress(course: str | None = None):
    return get_progress(course)


# ---------------------------------------------------------------------------
# Summary + Audio
# ---------------------------------------------------------------------------

class SummaryAudioRequest(BaseModel):
    topic: str | None = None
    course: str | None = None
    top_k: int = 5
    chunk_ids: list[int] = []
    speaker: str = "mari"


class TTSRequest(BaseModel):
    text: str
    speaker: str = "mari"


@app.post("/summary",
    summary="Generate an Estonian summary",
    description="Retrieves the most relevant material for a topic and summarises it in Estonian. "
               "Use `topic` for a search query or `chunk_ids` to pin specific chunks. "
               "Returns `{summary: str, sources: list}`. Feed the summary text to `/tts` for audio."
)
def summary(req: SummaryAudioRequest):
    result = build_summary(
        topic=req.topic,
        course=req.course,
        top_k=req.top_k,
        chunk_ids=req.chunk_ids or None,
    )
    return result


@app.post("/tts",
    summary="Text to Estonian speech (WAV)",
    description="Converts text to speech using TartuNLP (Estonian). "
               "Returns raw `audio/wav` bytes — play directly or save as `.wav`. "
               "Speakers: `mari` (female, default) or `kalev` (male)."
)
def tts(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    try:
        wav_bytes = synthesize(req.text, speaker=req.speaker)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=wav_bytes, media_type="audio/wav")


# ---------------------------------------------------------------------------
# Streaming RAG
# ---------------------------------------------------------------------------

@app.post("/rag/stream", summary="Stream RAG answer token by token")
def rag_stream_endpoint(req: RAGRequest):
    def event_stream():
        for chunk in rag_stream(req.question, course=req.course, top_k=req.top_k):
            yield f"data: {json.dumps({'token': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Study Plan
# ---------------------------------------------------------------------------

@app.post("/plan",
    summary="Generate a personalised study plan",
    description="Reads weak concepts from quiz history and builds a day-by-day schedule. "
               "Each day includes real reading references (document + page) and practice questions "
               "generated from your uploaded material. "
               "Requires at least one saved quiz result (`/quiz/result`) to have weak concepts."
)
def plan(req: PlanRequest):
    weak = get_weak_concepts(req.course, req.limit_weak)
    concept_names = [w["concept"] for w in weak]
    result = generate_plan(
        weak_concepts=concept_names,
        days_until_exam=req.days_until_exam,
        target_grade=req.target_grade,
        course=req.course,
    )
    return result


# ---------------------------------------------------------------------------
# Homework Helper
# ---------------------------------------------------------------------------

@app.post(
    "/homework-helper",
    summary="Guided hints without direct answers",
    description=(
        "Use this instead of `/rag` when the student is working on a homework problem "
        "and should find the answer themselves. "
        "The AI will point to relevant material, ask guiding questions, and give hints — "
        "**but will never reveal the direct answer**. "
        "Response: `{guidance: str, sources: list}`."
    ),
)
def homework_helper(req: HomeworkRequest):
    return homework_help(req.question, course=req.course, top_k=req.top_k)


# ---------------------------------------------------------------------------
# Spaced Repetition (SM-2)
# ---------------------------------------------------------------------------

@app.get("/quiz/next",
    summary="Get next concept for spaced repetition",
    description="Returns the concept most overdue for review according to SM-2. "
               "Falls back to unreviewed concepts if nothing is overdue. "
               "Use this to drive a 'Daily Review' feature. "
               "Returns `{concept, course, next_review, interval_days, repetitions, easiness}`."
)
def quiz_next(course: str | None = None):
    concept = get_next_concept(course)
    if not concept:
        return {"concept": None, "message": "No concepts due for review."}
    return concept


@app.post("/quiz/sm2",
    summary="Update SM-2 spaced repetition state",
    description="Call this after a quiz to schedule the next review. "
               "**Note:** you can skip this by passing `quality` directly to `/quiz/result`. "
               "Quality scale: 0-2 = failed (repeat soon), 3 = passed with difficulty, 4 = good, 5 = perfect."
)
def quiz_sm2(req: SM2UpdateRequest):
    if not 0 <= req.quality <= 5:
        raise HTTPException(status_code=400, detail="quality must be 0-5")
    return update_sm2(req.concept, req.course, req.quality)
