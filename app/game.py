from __future__ import annotations

import asyncio
import random
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .labels import LABELS

TOTAL_ROUNDS = 5
MAX_PLAYER_NAME_LENGTH = 40


def validate_player_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter your name, nickname, school, or team.")
    name = value.strip()
    if not name or len(name) > MAX_PLAYER_NAME_LENGTH:
        raise ValueError(f"Enter a name between 1 and {MAX_PLAYER_NAME_LENGTH} characters.")
    if not any(not char.isspace() and unicodedata.category(char)[0] not in {"C", "M"} for char in name):
        raise ValueError("Enter at least one visible character.")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("Control characters are not allowed in a name.")
    return name


@dataclass(frozen=True)
class RoundResult:
    round_number: int
    target: str
    score: float  # Percent, not a fraction.
    outcome: str
    elapsed_seconds: float


@dataclass(frozen=True)
class CompletedSession:
    session_id: str
    player_name: str
    score: float
    round_results: tuple[RoundResult, ...]


@dataclass
class GameState:
    duration: int
    threshold: float
    status: str = "waiting"
    player_name: str | None = None
    session_id: str | None = None
    session_targets: list[str] = field(default_factory=list)
    current_round: int = 0  # Zero-based internally.
    round_results: list[RoundResult] = field(default_factory=list)
    best_target_confidence: float = 0.0
    session_started_at: float | None = None
    session_completed: bool = False
    final_score: float | None = None
    leaderboard_rank: int | None = None
    target: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    outcome: str | None = None
    paths: list[list[dict[str, Any]]] = field(default_factory=list)
    predictions: list[dict[str, Any]] = field(default_factory=list)
    drawing_revision: int = 0
    predicted_revision: int = -1
    structure_revision: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def round_number(self) -> int:
        return self.current_round + 1 if self.session_id else 0

    def _start_round(self) -> None:
        self.status = "running"
        self.target = self.session_targets[self.current_round]
        self.started_at = time.time()
        self.ended_at = None
        self.outcome = None
        self.paths = []
        self.predictions = []
        self.best_target_confidence = 0.0
        self.drawing_revision += 1
        self.predicted_revision = self.drawing_revision
        self.structure_revision += 1

    async def start_session(self, player_name: object) -> bool:
        name = validate_player_name(player_name)
        async with self.lock:
            if self.status not in {"waiting", "session_complete"}:
                return False
            if len(LABELS) < TOTAL_ROUNDS:
                raise ValueError("At least five labels are required for a session.")
            self.player_name = name
            self.session_id = str(uuid.uuid4())
            self.session_targets = random.sample(LABELS, TOTAL_ROUNDS)
            self.current_round = 0
            self.round_results = []
            self.session_started_at = time.time()
            self.session_completed = False
            self.final_score = None
            self.leaderboard_rank = None
            self._start_round()
            return True

    async def next_round(self) -> bool:
        async with self.lock:
            if self.status != "round_complete" or self.current_round >= TOTAL_ROUNDS - 1:
                return False
            self.current_round += 1
            self._start_round()
            return True

    async def add_segment(self, segment: dict[str, Any]) -> dict[str, Any] | None:
        async with self.lock:
            if self.status != "running":
                return None
            clean = {
                key: max(0.0, min(1.0, float(segment[key])))
                for key in ("x1", "y1", "x2", "y2")
            }
            clean["width"] = max(0.001, min(0.08, float(segment.get("width", 0.014))))
            path_id = str(segment.get("pathId", "default"))[:80]
            if not self.paths or self.paths[-1][0].get("pathId") != path_id:
                clean["pathId"] = path_id
                self.paths.append([clean])
            else:
                clean["pathId"] = path_id
                self.paths[-1].append(clean)
            self.drawing_revision += 1
            return clean

    async def clear(self) -> None:
        async with self.lock:
            if self.status == "running":
                self.paths = []
                self.predictions = []
                self.drawing_revision += 1
                self.structure_revision += 1
                # best_target_confidence intentionally survives Clear.

    async def undo(self) -> None:
        async with self.lock:
            if self.status == "running" and self.paths:
                self.paths.pop()
                self.predictions = []
                self.drawing_revision += 1
                self.structure_revision += 1
                # best_target_confidence intentionally survives Undo.

    async def apply_predictions(
        self, all_predictions: list[dict[str, Any]], round_number: int, structure_revision: int
    ) -> tuple[bool, bool]:
        """Returns (accepted, AI recognized target). The model never receives target."""
        async with self.lock:
            if (self.status != "running" or self.round_number != round_number
                    or self.structure_revision != structure_revision or self.remaining() <= 0):
                return False, False
            self.predictions = all_predictions[:5]
            target_confidence = next(
                (float(item["confidence"]) for item in all_predictions if item["label"] == self.target), 0.0
            )
            self.best_target_confidence = max(self.best_target_confidence, target_confidence)
            top = all_predictions[0] if all_predictions else None
            ai_recognized = bool(
                top and top["label"] == self.target and float(top["confidence"]) >= self.threshold
            )
            return True, ai_recognized

    async def finish_round(self, outcome: str) -> tuple[bool, CompletedSession | None]:
        async with self.lock:
            if self.status != "running" or self.target is None or self.session_id is None:
                return False, None
            self.ended_at = time.time()
            self.outcome = outcome
            self.round_results.append(RoundResult(
                round_number=self.round_number,
                target=self.target,
                score=self.best_target_confidence * 100.0,
                outcome=outcome,
                elapsed_seconds=self.elapsed(),
            ))
            if len(self.round_results) == TOTAL_ROUNDS:
                self.session_completed = True
                self.final_score = sum(item.score for item in self.round_results) / TOTAL_ROUNDS
                self.status = "session_complete"
                return True, CompletedSession(
                    self.session_id, self.player_name or "", self.final_score, tuple(self.round_results)
                )
            self.status = "round_complete"
            return True, None

    async def set_leaderboard_rank(self, session_id: str, rank: int) -> None:
        async with self.lock:
            if self.session_id == session_id:
                self.leaderboard_rank = rank

    def elapsed(self, now: float | None = None) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at or now or time.time()
        return max(0.0, end - self.started_at)

    def remaining(self, now: float | None = None) -> float:
        return max(0.0, self.duration - self.elapsed(now))

    def snapshot(self, role: str, predictor_status: dict[str, Any]) -> dict[str, Any]:
        reveal = role == "draw" or self.status in {"round_complete", "session_complete"}
        return {
            "type": "state",
            "status": self.status,
            "playerName": self.player_name,
            "target": self.target if reveal else None,
            "round": self.round_number,
            "totalRounds": TOTAL_ROUNDS,
            "roundResults": [asdict(item) for item in self.round_results],
            "roundScore": self.round_results[-1].score if self.status in {"round_complete", "session_complete"} else None,
            "finalScore": self.final_score if self.session_completed else None,
            "leaderboardRank": self.leaderboard_rank if self.session_completed else None,
            "duration": self.duration,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "serverNow": time.time(),
            "remaining": self.remaining(),
            "elapsed": self.elapsed(),
            "outcome": self.outcome,
            "paths": self.paths,
            "predictions": self.predictions,
            "threshold": self.threshold,
            "predictor": predictor_status,
        }
