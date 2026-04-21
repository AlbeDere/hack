# Easels — RAG Study Companion

AI-powered study companion. Upload course PDFs, then ask questions, generate quizzes, get study plans, and listen to Estonian summaries.

**Live API:** `http://easels-api.dzhudjbeh3cpffdj.swedencentral.azurecontainer.io:8000`  
**Interactive docs (Swagger):** `http://easels-api.dzhudjbeh3cpffdj.swedencentral.azurecontainer.io:8000/docs`

All endpoints accept and return JSON. All `course` fields are optional filters — omit to search across all uploaded material.

---

## Endpoints

### `POST /ingest`

Upload a PDF and ingest it into the system. Uses `multipart/form-data`.

| Field    | Type   | Description                          |
| -------- | ------ | ------------------------------------ |
| `file`   | file   | PDF file                             |
| `title`  | string | Display name, e.g. `"Loeng 1"`       |
| `course` | string | Course name, e.g. `"Elektrisüsteem"` |

**Response:**

```json
{ "status": "ok", "title": "Loeng 1", "course": "Elektrisüsteem" }
```

---

### `GET /courses`

List all courses that have uploaded material.

```
GET /courses
```

**Response:**

```json
{ "courses": ["Andmebaasid", "Elektrisüsteem"] }
```

---

### `GET /concepts?course=Elektrisüsteem`

List all extracted concepts, optionally filtered by course.

**Response:**

```json
{ "concepts": ["alalisvool", "nimipinged", "põhivõrk", "jaotusvõrk"] }
```

---

### `POST /rag`

Ask a question. The AI retrieves relevant chunks from uploaded material and answers using only those sources. Supports multi-turn chat via `history`.

**Request:**

```json
{
  "question": "Mis on nimipinge?",
  "course": "Elektrisüsteem",
  "history": [
    { "role": "user", "content": "Mis on alalisvool?" },
    { "role": "assistant", "content": "Alalisvool on..." }
  ]
}
```

- `history` is optional — omit or send `[]` for a single-turn question.
- `top_k` (int, default `5`) — number of source chunks to retrieve.

**Response:**

```json
{
  "answer": "Nimipingeks nimetatakse pinget, millele elektriseade on ette nähtud [1].",
  "sources": [
    {
      "chunk_id": 12,
      "document_title": "Loeng 1",
      "page_number": 1,
      "text": "Nimipingeks nimetatakse pinget...",
      "score": 0.94
    }
  ]
}
```

---

### `POST /rag/stream`

Same as `/rag` but streams the answer token by token as Server-Sent Events. Use this for a typing effect in the UI. Sources are not returned — call `/rag` first if you need them.

**Request:** same as `/rag`

**Response:** `text/event-stream`

```
data: {"token": "Nimipingeks"}
data: {"token": " nimetatakse"}
...
data: [DONE]
```

---

### `POST /homework-helper`

Like `/rag` but the AI will **not give a direct answer**. Instead it gives guiding questions and hints, pointing to relevant material — useful when you want students to think, not copy.

**Request:**

```json
{
  "question": "Miks kasutatakse alalisvooluliine pikkade kaabelliinide korral?",
  "course": "Elektrisüsteem"
}
```

**Response:**

```json
{
  "guidance": "Mõtle sellele, mis juhtub kaabelliinides reaktiivvõimsusega vahelduvvoolu korral. Vaata allikat [1] — mis eeliseid mainitakse alalisvoolul?",
  "sources": [...]
}
```

---

### `POST /summary`

Generate an Estonian summary of the most relevant material for a topic.

**Request:**

```json
{
  "topic": "elektrisüsteemi struktuur",
  "course": "Elektrisüsteem",
  "top_k": 5
}
```

- `chunk_ids` (list of ints, optional) — pin specific chunks instead of searching by topic.

**Response:**

```json
{
  "summary": "Elektrisüsteem koosneb kolmest põhiosast: tootmine, ülekanne ja jaotus [1][2]...",
  "sources": [...]
}
```

---

### `POST /tts`

Convert text to Estonian speech. Returns a WAV audio file.

**Request:**

```json
{
  "text": "Nimipingeks nimetatakse pinget, millele elektriseade on ette nähtud.",
  "speaker": "mari"
}
```

- `speaker`: `"mari"` (default female) or `"kalev"` (male)

**Response:** `audio/wav` binary — play directly or save as `.wav`

**Recommended flow:** Call `/summary` → display text → on user click call `/tts` with the summary text.

---

### `POST /quiz/generate`

Generate quiz questions for a concept, grounded in the uploaded material.

**Request:**

```json
{
  "concept": "nimipinged",
  "course": "Elektrisüsteem",
  "n_questions": 3
}
```

**Response:**

```json
{
  "questions": [
    {
      "question": "Millised nimipinged vastavad IEC standarditele Eesti kõrgepingevõrkudes?",
      "source_text": "Eesti kõrgepingevõrkudes vastavad IEC-standarditele nimipinged 10, 20, 35, 110 ja 220 kV..."
    }
  ]
}
```

Store `source_text` — you need it for `/quiz/evaluate`.

---

### `POST /quiz/evaluate`

Evaluate a student's answer to a quiz question. Returns feedback and a score.

**Request:**

```json
{
  "question": "Millised nimipinged vastavad IEC standarditele?",
  "student_answer": "10, 20, 35, 110 ja 220 kV",
  "source_text": "Eesti kõrgepingevõrkudes vastavad IEC-standarditele nimipinged 10, 20, 35, 110 ja 220 kV..."
}
```

**Response:**

```json
{
  "correct": true,
  "score": 1,
  "feedback": "Õige! Kõik IEC-standarditele vastavad nimipinged on nimetatud."
}
```

---

### `POST /quiz/result`

Save the result of a completed quiz attempt. Used to track weak concepts and power the study plan.

**Request:**

```json
{
  "concept": "nimipinged",
  "course": "Elektrisüsteem",
  "score": 2,
  "total": 3
}
```

**Response:**

```json
{ "percentage": 66.7, "is_personal_best": true }
```

---

### `GET /quiz/weak?course=Elektrisüsteem&limit=5`

Get the weakest concepts based on quiz history (lowest average score).

**Response:**

```json
{
  "weak_concepts": [
    { "concept": "alalisvool", "avg_percentage": 33.3, "attempts": 2 },
    { "concept": "nimipinged", "avg_percentage": 66.7, "attempts": 1 }
  ]
}
```

---

### `GET /quiz/next?course=Elektrisüsteem`

Get the next concept due for spaced repetition review (SM-2 algorithm). Call this to drive a "Daily Review" feature.

**Response (concept due):**

```json
{
  "concept": "alalisvool",
  "course": "Elektrisüsteem",
  "next_review": "2026-04-22",
  "interval_days": 1,
  "repetitions": 2,
  "easiness": 2.3
}
```

**Response (nothing due):**

```json
{ "concept": null, "message": "No concepts due for review." }
```

---

### `POST /quiz/sm2`

Update SM-2 spaced repetition state after a quiz attempt. Call this after each quiz session to schedule the next review.

**Request:**

```json
{
  "concept": "alalisvool",
  "course": "Elektrisüsteem",
  "quality": 4
}
```

| `quality` | Meaning                      |
| --------- | ---------------------------- |
| 0–2       | Failed — will repeat soon    |
| 3         | Passed but difficult         |
| 4         | Correct with some hesitation |
| 5         | Perfect recall               |

**Response:**

```json
{
  "concept": "alalisvool",
  "next_review": "2026-04-28",
  "interval_days": 6,
  "easiness": 2.5,
  "repetitions": 3
}
```

---

### `POST /plan`

Generate a day-by-day study plan grounded in actual uploaded material. Automatically picks the weakest concepts from quiz history.

**Request:**

```json
{
  "course": "Elektrisüsteem",
  "days_until_exam": 5,
  "target_grade": "4",
  "limit_weak": 10
}
```

- Only `days_until_exam` is required.

**Response:**

```json
{
  "plan": "Focus on alalisvool and nimipinged over the next 4 study days...",
  "schedule": [
    {
      "day": 1,
      "date_label": "Day 1",
      "concepts": ["alalisvool"],
      "reading": [
        {
          "document": "Loeng 1",
          "page": 2,
          "excerpt": "Alalisvoolu korral on nii õhu- kui kaabelliinid lihtsamad..."
        }
      ],
      "questions": [
        {
          "question": "Miks kasutatakse alalisvooluliine pikkade merealuste kaabelliinide korral?",
          "source_text": "..."
        }
      ]
    },
    {
      "day": 5,
      "date_label": "Day 5 — Review",
      "concepts": ["alalisvool", "nimipinged"],
      "reading": [],
      "questions": [],
      "note": "Review all concepts. Re-read excerpts you found difficult. Redo any questions you got wrong."
    }
  ]
}
```

---

## Typical Frontend Flows

### Chat / Q&A

```
user types question
→ POST /rag (with history array)
→ display answer + highlight sources
→ append {role:"user", content:question} and {role:"assistant", content:answer} to history
→ repeat
```

### Quiz Session

```
GET /quiz/next (or let user pick concept)
→ POST /quiz/generate
→ show questions one by one, collect answers
→ POST /quiz/evaluate per answer → show feedback
→ POST /quiz/result (total score)
→ POST /quiz/sm2 (quality 0-5 based on percentage)
```

### Summary + Audio

```
POST /summary → display text
user clicks "Listen"
→ POST /tts with summary text → play WAV
```

### Study Plan

```
(requires some quiz results saved first via POST /quiz/result)
POST /plan → display day-by-day schedule with reading refs and practice questions
```

---

## Backend Setup (for devs)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # fill in Azure keys
.venv\Scripts\python.exe -m db.init_db
uvicorn main:app --reload
```

Ingest a PDF:

```powershell
# via API (recommended)
curl -X POST http://localhost:8000/ingest \
  -F "file=@konspekt.pdf" -F "title=Loeng 1" -F "course=Elektrisüsteem"
```

## Project Structure

```
├── main.py             # FastAPI app, all endpoints
├── ingest.py           # PDF → chunks → embeddings → concepts → DB
├── query.py            # Hybrid vector + FTS5 search (RRF)
├── rag.py              # Retrieval + LLM generation
├── quiz.py             # Question generation, evaluation, results
├── plan.py             # Study plan with real chunk references
├── summary.py          # Estonian summary generation
├── speech.py           # TartuNLP TTS (public API)
├── homework_helper.py  # Guided hints without direct answers
├── sm2.py              # SM-2 spaced repetition algorithm
├── llm.py              # Azure OpenAI client
└── db/
    ├── schema.sql      # Table definitions
    ├── init_db.py      # Run once to create tables
    └── connection.py   # SQLite + sqlite-vec connection
```
