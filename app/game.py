from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .labels import LABELS


@dataclass
class GameState:
    duration: int
    threshold: float
    status: str = "waiting"
    target: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    outcome: str | None = None
    paths: list[list[dict[str, float]]] = field(default_factory=list)
    predictions: list[dict[str, Any]] = field(default_factory=list)
    drawing_revision: int = 0
    predicted_revision: int = -1
    structure_revision: int = 0
    round_number: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def start(self) -> None:
        async with self.lock:
            self.status = "running"
            self.target = random.choice(LABELS)
            self.started_at = time.time()
            self.ended_at = None
            self.outcome = None
            self.paths = []
            self.predictions = []
            self.drawing_revision += 1
            # A new round has no ink yet; the first accepted stroke marks it dirty.
            self.predicted_revision = self.drawing_revision
            self.structure_revision += 1
            self.round_number += 1

    async def add_segment(self, segment: dict[str, Any]) -> dict[str, float] | None:
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

    async def undo(self) -> None:
        async with self.lock:
            if self.status == "running" and self.paths:
                self.paths.pop()
                self.predictions = []
                self.drawing_revision += 1
                self.structure_revision += 1

    async def end(self, outcome: str) -> None:
        async with self.lock:
            if self.status == "running":
                self.status = "won" if outcome == "ai_won" else "lost"
                self.outcome = outcome
                self.ended_at = time.time()

    def elapsed(self, now: float | None = None) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at or now or time.time()
        return max(0.0, end - self.started_at)

    def remaining(self, now: float | None = None) -> float:
        return max(0.0, self.duration - self.elapsed(now))

    def snapshot(self, role: str, predictor_status: dict[str, Any]) -> dict[str, Any]:
        reveal = role == "draw" or self.status in {"won", "lost"}
        return {
            "type": "state",
            "status": self.status,
            "target": self.target if reveal else None,
            "duration": self.duration,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "serverNow": time.time(),
            "remaining": self.remaining(),
            "elapsed": self.elapsed(),
            "outcome": self.outcome,
            "paths": self.paths,
            "predictions": self.predictions,
            "round": self.round_number,
            "threshold": self.threshold,
            "predictor": predictor_status,
        }
