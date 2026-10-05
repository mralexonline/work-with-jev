"""Small admin CLI: python -m app.cli show-token | cleanup | usage"""
from __future__ import annotations

import sys

from . import budget, db, jobq
from .config import get_settings


def main(argv: list[str]) -> int:
    db.init()
    cmd = argv[0] if argv else "help"
    if cmd == "show-token":
        print(get_settings().owner_token)
    elif cmd == "usage":
        import json
        print(json.dumps(budget.summary(), indent=2))
    elif cmd == "cleanup":
        n = jobq.requeue_expired()
        print(f"re-queued/failed {n} expired job lease(s)")
    else:
        print("usage: python -m app.cli [show-token|usage|cleanup]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
