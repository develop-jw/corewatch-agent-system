"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "session.db"


class SqliteSessionStore:
    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS context (thread_id TEXT PRIMARY KEY, alarm TEXT)"
        )
        self.conn.commit()

    def load(self, thread_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid",
            (thread_id,),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def append(self, thread_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?)",
            (thread_id, role, content, datetime.now().isoformat()),
        )
        self.conn.commit()

    def save_alarm(self, thread_id: str, alarm: dict) -> None:
        """이어지는 질문용 — 이 대화에서 마지막으로 분석한 알람을 기억한다."""
        self.conn.execute("INSERT OR REPLACE INTO context VALUES (?,?)",
                          (thread_id, json.dumps(alarm, ensure_ascii=False)))
        self.conn.commit()

    def load_alarm(self, thread_id: str) -> dict | None:
        row = self.conn.execute("SELECT alarm FROM context WHERE thread_id=?", (thread_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self.conn.close()
