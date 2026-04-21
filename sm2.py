"""SM-2 spaced repetition algorithm for concept review scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta
from db.connection import get_connection


def _ensure_table():
    """Create sm2_state table if it doesn't exist yet (migration-safe)."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sm2_state (
                concept TEXT NOT NULL,
                course TEXT NOT NULL,
                easiness REAL NOT NULL DEFAULT 2.5,
                interval INTEGER NOT NULL DEFAULT 1,
                repetitions INTEGER NOT NULL DEFAULT 0,
                next_review TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (concept, course)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def update_sm2(concept: str, course: str, quality: int) -> dict:
    """
    Update SM-2 state after a quiz attempt.
    quality: 0-5 (0-2 = fail, 3-5 = pass, 5 = perfect)
    Returns updated state dict.
    """
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT easiness, interval, repetitions FROM sm2_state WHERE concept=? AND course=?",
            (concept, course),
        ).fetchone()

        if row:
            easiness = row["easiness"]
            interval = row["interval"]
            repetitions = row["repetitions"]
        else:
            easiness = 2.5
            interval = 1
            repetitions = 0

        # SM-2 algorithm
        if quality < 3:
            repetitions = 0
            interval = 1
        else:
            if repetitions == 0:
                interval = 1
            elif repetitions == 1:
                interval = 6
            else:
                interval = round(interval * easiness)
            repetitions += 1

        easiness = max(1.3, easiness + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        next_review = (datetime.utcnow() + timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            """
            INSERT INTO sm2_state (concept, course, easiness, interval, repetitions, next_review)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(concept, course) DO UPDATE SET
                easiness=excluded.easiness,
                interval=excluded.interval,
                repetitions=excluded.repetitions,
                next_review=excluded.next_review
            """,
            (concept, course, easiness, interval, repetitions, next_review),
        )
        conn.commit()
        return {
            "concept": concept,
            "course": course,
            "easiness": round(easiness, 3),
            "interval_days": interval,
            "repetitions": repetitions,
            "next_review": next_review,
        }
    finally:
        conn.close()


def get_next_concept(course: str | None = None) -> dict | None:
    """
    Return the concept most overdue for review.
    Falls back to concepts with no SM-2 state (never reviewed).
    """
    _ensure_table()
    conn = get_connection()
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if course:
            row = conn.execute(
                """
                SELECT concept, course, next_review, interval, repetitions, easiness
                FROM sm2_state
                WHERE course = ? AND next_review <= ?
                ORDER BY next_review ASC
                LIMIT 1
                """,
                (course, now),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT concept, course, next_review, interval, repetitions, easiness
                FROM sm2_state
                WHERE next_review <= ?
                ORDER BY next_review ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()

        if row:
            return dict(row)

        # Fall back: concept with no SM-2 state at all
        if course:
            fallback = conn.execute(
                """
                SELECT name AS concept, course FROM concepts
                WHERE course = ?
                AND name NOT IN (SELECT concept FROM sm2_state WHERE course = ?)
                LIMIT 1
                """,
                (course, course),
            ).fetchone()
        else:
            fallback = conn.execute(
                """
                SELECT name AS concept, course FROM concepts
                WHERE name NOT IN (SELECT concept FROM sm2_state)
                LIMIT 1
                """
            ).fetchone()

        if fallback:
            return {"concept": fallback["concept"], "course": fallback["course"], "next_review": None, "interval_days": None, "repetitions": 0, "easiness": 2.5}

        return None
    finally:
        conn.close()
