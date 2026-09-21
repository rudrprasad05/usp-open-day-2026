"""Local SQLite leaderboard. A session ID has exactly one durable entry."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .game import CompletedSession, TOTAL_ROUNDS

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "drawai.db"


class Leaderboard:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS leaderboard_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL UNIQUE,
                    player_name TEXT NOT NULL,
                    score REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    rounds_json TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS leaderboard_order
                ON leaderboard_entries (score DESC, created_at ASC, id ASC)
            """)

    def save_session(self, session: CompletedSession) -> tuple[int, int]:
        if len(session.round_results) != TOTAL_ROUNDS:
            raise ValueError("Only completed five-round sessions may enter the leaderboard")
        rounds = [
            {"roundNumber": item.round_number, "target": item.target,
             "score": item.score, "outcome": item.outcome, "elapsedSeconds": item.elapsed_seconds}
            for item in session.round_results
        ]
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT OR IGNORE INTO leaderboard_entries
                   (session_id, player_name, score, created_at, rounds_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (session.session_id, session.player_name, session.score,
                 datetime.now(timezone.utc).isoformat(timespec="microseconds"), json.dumps(rounds)),
            )
            row = connection.execute(
                "SELECT id FROM leaderboard_entries WHERE session_id = ?", (session.session_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("Completed session was not saved")
            entry_id = int(row["id"])
            ordered = connection.execute(
                "SELECT id FROM leaderboard_entries ORDER BY score DESC, created_at ASC, id ASC"
            ).fetchall()
            rank = next(index for index, item in enumerate(ordered, 1) if item["id"] == entry_id)
            return entry_id, rank

    def top(self, limit: int = 10) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT id, player_name, score, created_at FROM leaderboard_entries
                   ORDER BY score DESC, created_at ASC, id ASC LIMIT ?""", (limit,)
            ).fetchall()
        return [
            {"rank": rank, "id": row["id"], "playerName": row["player_name"],
             "score": row["score"], "createdAt": row["created_at"]}
            for rank, row in enumerate(rows, 1)
        ]

    def count(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM leaderboard_entries").fetchone()[0])

    def reset(self) -> int:
        with closing(self._connect()) as connection, connection:
            count = int(connection.execute("SELECT COUNT(*) FROM leaderboard_entries").fetchone()[0])
            connection.execute("DELETE FROM leaderboard_entries")
            return count
