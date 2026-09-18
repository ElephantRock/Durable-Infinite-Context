from __future__ import annotations

import argparse
import os
import signal

from storage.bidirectional_queued_retirement_descriptor_primary import (
    BidirectionalQueuedRetirementDescriptorPrimaryStore,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("command", choices=("insert", "reclaim", "evacuate"))
    parser.add_argument("--key")
    parser.add_argument("--budget", type=int, default=2)
    parser.add_argument("--failpoint", required=True)
    args = parser.parse_args()

    def crash(name: str) -> None:
        if name == args.failpoint:
            os.kill(os.getpid(), signal.SIGKILL)

    store = BidirectionalQueuedRetirementDescriptorPrimaryStore(args.file)
    if args.command == "insert":
        if args.key is None:
            raise SystemExit("insert requires --key")
        store.insert(args.key, failpoint=crash)
    elif args.command == "reclaim":
        store.reclaim_step(budget=args.budget, failpoint=crash)
    else:
        store.evacuate_bidirectional_queued_live_retirement_arena_tail_step(
            failpoint=crash
        )


if __name__ == "__main__":
    main()
