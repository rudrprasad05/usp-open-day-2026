"""Interactive local-only leaderboard reset: python -m app.reset_leaderboard."""

from pathlib import Path

from .config import settings
from .leaderboard import Leaderboard


def main() -> None:
    leaderboard = Leaderboard(Path(settings.leaderboard_db_path)) if settings.leaderboard_db_path else Leaderboard()
    leaderboard.initialize()
    count = leaderboard.count()
    answer = input(f"Delete all {count} leaderboard entries? [y/N] ").strip().lower()
    if answer != "y":
        print("Cancelled; no entries were deleted.")
        return
    deleted = leaderboard.reset()
    print(f"Deleted {deleted} leaderboard entries. This cannot be undone without a database backup.")


if __name__ == "__main__":
    main()
