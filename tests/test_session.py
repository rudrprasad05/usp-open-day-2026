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
        game = GameState(30)
        self.assertTrue(await game.start_session("  Team Dragons  "))
        self.assertEqual(game.player_name, "Team Dragons")
        self.assertEqual(len(game.session_targets), TOTAL_ROUNDS)
        self.assertEqual(len(set(game.session_targets)), TOTAL_ROUNDS)
        public = game.snapshot("display", {"ready": True})
        self.assertIsNone(public["target"])
        self.assertNotIn("session_targets", public)
        self.assertEqual(game.snapshot("draw", {"ready": True})["target"], game.target)

    async def test_high_live_confidence_never_ends_round_and_final_score_is_fresh(self):
        game = GameState(30)
        await game.start_session("Rudr")
        target = game.target
        self.assertTrue(await game.apply_predictions(
            [{"label": target, "confidence": .95}], 1, game.structure_revision
        ))
        self.assertEqual(game.status, "running")
        segment = {"x1": .1, "y1": .2, "x2": .3, "y2": .4, "pathId": "path"}
        self.assertIsNotNone(await game.add_segment(segment))
        await game.undo()
        await game.clear()
        self.assertIsNotNone(await game.add_segment(segment))
        job = await game.begin_finalization("done")
        self.assertIsNotNone(job)
        self.assertEqual(game.status, "finalizing")
        self.assertEqual(len(job.paths), 1)
        self.assertIsNone(await game.add_segment(segment))
        self.assertIsNone(await game.begin_finalization("done"))
        self.assertIsNone(game.snapshot("display", {"ready": True})["target"])
        # The target is outside the public top five, but still receives its final score.
        final_scores = [{"label": f"other-{index}", "confidence": .6 - index * .01} for index in range(7)]
        final_scores.append({"label": target, "confidence": .42})
        finished, completed = await game.complete_finalization(job, final_scores)
        self.assertTrue(finished)
        self.assertIsNone(completed)
        self.assertAlmostEqual(game.round_results[0].score, 42.0)
        self.assertEqual(game.round_results[0].outcome, "done")
        self.assertEqual(game.status, "round_complete")
        self.assertFalse((await game.complete_finalization(job, final_scores))[0])
        self.assertEqual(len(game.round_results), 1)

    async def test_timeout_uses_same_finalization_and_scores_once(self):
        game = GameState(30)
        await game.start_session("Timeout player")
        target = game.target
        job = await game.begin_finalization("timeout")
        self.assertIsNotNone(job)
        self.assertIsNone(await game.begin_finalization("done"))
        finished, completed = await game.complete_finalization(job, [{"label": target, "confidence": .37}])
        self.assertTrue(finished)
        self.assertIsNone(completed)
        self.assertEqual(game.round_results[0].outcome, "timeout")
        self.assertAlmostEqual(game.round_results[0].score, 37.0)
        self.assertFalse((await game.complete_finalization(job, []))[0])
        self.assertEqual(len(game.round_results), 1)

    async def test_five_round_average_and_no_duplicate_finish(self):
        game = GameState(30)
        await game.start_session("USP Engineering")
        target_set = set(game.session_targets)
        values = [.82, .61, .94, .73, .90]
        completed = None
        for index, value in enumerate(values, 1):
            self.assertEqual(game.round_number, index)
            # Live guesses cannot influence the official score.
            await game.apply_predictions([{"label": game.target, "confidence": 1.0}], index, game.structure_revision)
            job = await game.begin_finalization("done")
            self.assertIsNotNone(job)
            finished, completed = await game.complete_finalization(
                job, [{"label": game.target, "confidence": value}]
            )
            self.assertTrue(finished)
            if index < TOTAL_ROUNDS:
                self.assertIsNone(completed)
                self.assertTrue(await game.next_round())
                self.assertEqual(game.predictions, [])
            else:
                self.assertIsNotNone(completed)
        self.assertEqual(len(target_set), TOTAL_ROUNDS)
        self.assertEqual(len(game.round_results), TOTAL_ROUNDS)
        self.assertAlmostEqual(game.final_score, 80.0)
        self.assertEqual(game.status, "session_complete")
        self.assertIsNone(await game.begin_finalization("done"))
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
