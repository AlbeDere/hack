"""
Easels API — FastAPI wrapper around the RAG study companion.

Endpoints:
  POST /ingest              — upload a PDF and ingest it
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
    question: str
    course: str | None = None
    top_k: int = 5
    history: list[dict] = []  # [{"role": "user"|"assistant", "content": "..."}]


class RAGResponse(BaseModel):
    answer: str
    sources: list[dict]


class QuizGenerateRequest(BaseModel):
    concept: str
    course: str | None = None
    n_questions: int = 5


class QuizEvaluateRequest(BaseModel):
    question: str
    student_answer: str
    source_text: str


class QuizResultRequest(BaseModel):
    concept: str
    course: str | None = None
    score: int
    total: int


class PlanRequest(BaseModel):
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
    concept: str
    course: str
    quality: int  # 0-5


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@app.post("/ingest", summary="Upload and ingest a PDF")
async def ingest_pdf(
    file: UploadFile = File(...),
    title: str = Form(...),
    course: str = Form(...),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Save upload to a temp file then ingest
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

@app.post("/rag", response_model=RAGResponse, summary="Ask a question")
def ask(req: RAGRequest):
    result = rag(req.question, course=req.course, top_k=req.top_k, history=req.history)
    return result


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------

@app.post("/quiz/generate", summary="Generate quiz questions for a concept")
def quiz_generate(req: QuizGenerateRequest):
    chunks = get_chunks_for_concept(req.concept, req.course)
    if not chunks:
        raise HTTPException(
            status_code=404,
            detail=f"No material found for concept '{req.concept}'.",
        )
    questions = generate_questions(chunks, n=req.n_questions, concept=req.concept)
    if not questions:
        raise HTTPException(status_code=500, detail="Could not generate questions.")
    return {"questions": questions}


@app.post("/quiz/evaluate", summary="Evaluate a student answer")
def quiz_evaluate(req: QuizEvaluateRequest):
    result = evaluate_answer(req.question, req.student_answer, req.source_text)
    return result


@app.post("/quiz/result", summary="Save a completed quiz result")
def quiz_result(req: QuizResultRequest):
    pct, is_best = save_quiz_result(
        req.concept, req.course or "", req.score, req.total
    )
    return {"percentage": pct, "is_personal_best": bool(is_best)}


@app.get("/quiz/weak", summary="Get weakest concepts by quiz history")
def quiz_weak(course: str | None = None, limit: int = 5):
    return {"weak_concepts": get_weak_concepts(course, limit)}


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


@app.post("/summary", summary="Estonian text summary from retrieved chunks")
def summary(req: SummaryAudioRequest):
    result = build_summary(
        topic=req.topic,
        course=req.course,
        top_k=req.top_k,
        chunk_ids=req.chunk_ids or None,
    )
    return result


@app.post("/tts", summary="Convert text to Estonian speech (WAV)")
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

@app.post("/plan", summary="Generate a study plan based on weak concepts")
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

@app.get("/quiz/next", summary="Get next concept due for review (SM-2)")
def quiz_next(course: str | None = None):
    concept = get_next_concept(course)
    if not concept:
        return {"concept": None, "message": "No concepts due for review."}
    return concept


@app.post("/quiz/sm2", summary="Update SM-2 state after a quiz attempt")
def quiz_sm2(req: SM2UpdateRequest):
    if not 0 <= req.quality <= 5:
        raise HTTPException(status_code=400, detail="quality must be 0-5")
    return update_sm2(req.concept, req.course, req.quality)
