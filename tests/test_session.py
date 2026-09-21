from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.game import CompletedSession, GameState, RoundResult, TOTAL_ROUNDS, validate_player_name
from app.leaderboard import Leaderboard


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_name_validation_and_five_unique_targets(self):
        for invalid in (None, "", " \t ", "\u200b", "\u0301", "x" * 41):
            with self.assertRaises(ValueError):
                validate_player_name(invalid)
        game = GameState(30, .55)
        self.assertTrue(await game.start_session("  Team Dragons  "))
        self.assertEqual(game.player_name, "Team Dragons")
        self.assertEqual(len(game.session_targets), TOTAL_ROUNDS)
        self.assertEqual(len(set(game.session_targets)), TOTAL_ROUNDS)
        public = game.snapshot("display", {"ready": True})
        self.assertIsNone(public["target"])
        self.assertNotIn("session_targets", public)
        self.assertEqual(game.snapshot("draw", {"ready": True})["target"], game.target)

    async def test_target_outside_top_five_best_score_clear_undo_and_timeout(self):
        game = GameState(30, .55)
        await game.start_session("Rudr")
        target = game.target
        other = next(label for label in game.session_targets if label != target)
        scores = [{"label": f"other-{index}", "confidence": .12} for index in range(7)]
        scores.append({"label": target, "confidence": .07})
        accepted, won = await game.apply_predictions(scores, 1, game.structure_revision)
        self.assertTrue(accepted)
        self.assertFalse(won)
        self.assertAlmostEqual(game.best_target_confidence, .07)
        self.assertNotIn(target, [item["label"] for item in game.predictions])
        await game.add_segment({"x1": .1, "y1": .2, "x2": .3, "y2": .4, "pathId": "path"})
        higher = [{"label": other, "confidence": .8}, {"label": target, "confidence": .71}]
        await game.apply_predictions(higher, 1, game.structure_revision)
        await game.undo()
        await game.clear()
        lower = [{"label": other, "confidence": .8}, {"label": target, "confidence": .42}]
        await game.apply_predictions(lower, 1, game.structure_revision)
        self.assertAlmostEqual(game.best_target_confidence, .71)
        finished, completed = await game.finish_round("timeout")
        self.assertTrue(finished)
        self.assertIsNone(completed)
        self.assertAlmostEqual(game.round_results[0].score, 71.0)
        self.assertEqual(game.status, "round_complete")
        self.assertFalse((await game.finish_round("timeout"))[0])

    async def test_five_round_average_and_no_duplicate_finish(self):
        game = GameState(30, .55)
        await game.start_session("USP Engineering")
        target_set = set(game.session_targets)
        values = [.82, .61, .94, .73, .90]
        completed = None
        for index, value in enumerate(values, 1):
            self.assertEqual(game.round_number, index)
            await game.apply_predictions([{"label": game.target, "confidence": value}], index, game.structure_revision)
            finished, completed = await game.finish_round("done")
            self.assertTrue(finished)
            if index < TOTAL_ROUNDS:
                self.assertIsNone(completed)
                self.assertTrue(await game.next_round())
                self.assertEqual(game.best_target_confidence, 0.0)
            else:
                self.assertIsNotNone(completed)
        self.assertEqual(len(target_set), TOTAL_ROUNDS)
        self.assertEqual(len(game.round_results), TOTAL_ROUNDS)
        self.assertAlmostEqual(game.final_score, 80.0)
        self.assertEqual(game.status, "session_complete")
        self.assertFalse((await game.finish_round("done"))[0])
        self.assertFalse(await game.next_round())
        self.assertIsNotNone(completed)


class LeaderboardTests(unittest.TestCase):
    def test_persistence_sorting_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drawai.db"
            board = Leaderboard(path)
            board.initialize()
            rounds = tuple(RoundResult(i, f"object-{i}", score, "done", 5.0) for i, score in enumerate([80.0] * 5, 1))
            first = CompletedSession("first", "Maya", 80.0, rounds)
            second = CompletedSession("second", "Team Dragons", 91.4, rounds)
            tie = CompletedSession("tie", "Jai", 80.0, rounds)
            board.save_session(first)
            board.save_session(second)
            board.save_session(first)
            board.save_session(tie)
            self.assertEqual(board.count(), 3)
            reopened = Leaderboard(path)
            reopened.initialize()
            entries = reopened.top()
            self.assertEqual([item["playerName"] for item in entries], ["Team Dragons", "Maya", "Jai"])
            self.assertEqual([item["rank"] for item in entries], [1, 2, 3])
            self.assertEqual(reopened.top(1)[0]["playerName"], "Team Dragons")


if __name__ == "__main__":
    unittest.main()
