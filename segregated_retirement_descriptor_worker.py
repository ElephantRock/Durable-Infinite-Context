from __future__ import annotations

import argparse
import json
import os
import signal

from storage.segregated_retirement_descriptor_primary import (
    SegregatedRetirementDescriptorPrimaryStore,
)

INSERT_FAILPOINTS = {
    "allocated",
    "reused_extent_scrubbed",
    "retirement_arena_descriptor_written",
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "pages_written",
    "data_synced",
    "committed",
}
RECLAIM_FAILPOINTS = {
    "mapping_unlinked",
    "free_header_written",
    "retirement_descriptor_updated",
    "retirement_descriptor_freed",
    "retirement_arena_synced",
    "retirement_arena_reset_staged",
    "retirement_dequeued",
    "dependencies_synced",
    "committed",
    "retirement_arena_reset_committed",
    "retirement_arena_truncated",
    "retirement_arena_reset_synced",
}


def abrupt_kill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    insert = sub.add_parser("insert")
    insert.add_argument("--key", required=True)
    insert.add_argument("--failpoint", choices=sorted(INSERT_FAILPOINTS), required=True)

    reclaim = sub.add_parser("reclaim")
    reclaim.add_argument("--budget", type=int, required=True)
    reclaim.add_argument("--failpoint", choices=sorted(RECLAIM_FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--key", action="append", default=[])
    sub.add_parser("recover")

    args = parser.parse_args()
    store = SegregatedRetirementDescriptorPrimaryStore(args.file)

    if args.command == "insert":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()

        store.insert(args.key, failpoint=failpoint)
        raise AssertionError("requested insert failpoint was not reached")

    if args.command == "reclaim":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()

        store.reclaim_step(budget=args.budget, failpoint=failpoint)
        raise AssertionError("requested reclaim failpoint was not reached")

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
