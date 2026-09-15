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


def abrupt_kill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    shrink = sub.add_parser("shrink")
    shrink.add_argument("--failpoint", choices=sorted(SHRINK_FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--key", action="append", default=[])
    sub.add_parser("recover")

    args = parser.parse_args()
    store = BidirectionalRetirementDescriptorPrimaryStore(args.file)

    if args.command == "shrink":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()

        store.shrink_retirement_arena_tail_step(failpoint=failpoint)
        raise AssertionError("requested shrink failpoint was not reached")

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
