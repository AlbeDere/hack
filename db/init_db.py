"""Run this once to initialise the SQLite database."""

from db.connection import get_connection

def _load_schema() -> str:
    with open("db/schema.sql") as f:
        # Strip comment lines so semicolons inside comments don't break splitting
        lines = [l for l in f if not l.strip().startswith("--")]
    return "".join(lines)


def init():
    conn = get_connection()
    for statement in _load_schema().split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    conn.close()
    print("Database initialised.")


if __name__ == "__main__":
    init()
