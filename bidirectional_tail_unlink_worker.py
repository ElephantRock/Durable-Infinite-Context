from __future__ import annotations

import argparse
import json
import os
import signal

from storage.bidirectional_retirement_descriptor_primary import (
    BidirectionalRetirementDescriptorPrimaryStore,
)

SHRINK_FAILPOINTS = {
    "retirement_tail_unlink_staged",
    "retirement_tail_unlink_dependencies_synced",
    "committed",
    "retirement_tail_unlink_committed",
    "retirement_arena_truncated",
    "retirement_tail_unlink_synced",
}
RECLAIM_FAILPOINTS = {
    "retirement_descriptor_freed",
    "retirement_arena_synced",
    "dependencies_synced",
    "committed",
}
INSERT_FAILPOINTS = {
    "retirement_descriptor_reused",
    "retirement_arena_synced",
    "data_synced",
    "committed",
}


def abrupt_kill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def killer(expected: str):
    def failpoint(stage: str) -> None:
        if stage == expected:
            abrupt_kill()
    return failpoint


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    shrink = sub.add_parser("shrink")
    shrink.add_argument("--failpoint", choices=sorted(SHRINK_FAILPOINTS), required=True)

    reclaim = sub.add_parser("reclaim")
    reclaim.add_argument("--budget", type=int, required=True)
    reclaim.add_argument("--failpoint", choices=sorted(RECLAIM_FAILPOINTS), required=True)

    insert = sub.add_parser("insert")
    insert.add_argument("--key", required=True)
    insert.add_argument("--failpoint", choices=sorted(INSERT_FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--key", action="append", default=[])
    sub.add_parser("recover")

    args = parser.parse_args()
    store = BidirectionalRetirementDescriptorPrimaryStore(args.file)

    if args.command == "shrink":
        store.shrink_retirement_arena_tail_step(failpoint=killer(args.failpoint))
        raise AssertionError("requested shrink failpoint was not reached")

    if args.command == "reclaim":
        store.reclaim_step(budget=args.budget, failpoint=killer(args.failpoint))
        raise AssertionError("requested reclaim failpoint was not reached")

    if args.command == "insert":
        store.insert(args.key, failpoint=killer(args.failpoint))
        raise AssertionError("requested insert failpoint was not reached")

    if args.command == "inspect":
        print(
            json.dumps(
                {
                    "queue": store.retirement_queue_snapshot(),
                    "state": store.committed_state(tuple(args.key)),
                    "arena": store.descriptor_arena_diagnostic(),
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "recover":
        print(json.dumps(store.recover(), sort_keys=True))
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
