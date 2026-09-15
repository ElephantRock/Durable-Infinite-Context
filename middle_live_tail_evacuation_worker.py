from __future__ import annotations

import argparse
import json
import os
import signal

from storage.middle_live_tail_evacuation_retirement_descriptor_primary import (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)

EVACUATION_FAILPOINTS = {
    "retirement_middle_tail_destination_staged",
    "retirement_middle_tail_predecessor_staged",
    "retirement_middle_tail_dependencies_synced",
    "committed",
    "retirement_middle_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_middle_tail_relocation_synced",
}

REUSE_FAILPOINTS = {
    "retirement_descriptor_reused",
    "retirement_tail_linked",
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

    evacuate = sub.add_parser("evacuate")
    evacuate.add_argument("--failpoint", choices=sorted(EVACUATION_FAILPOINTS), required=True)

    insert = sub.add_parser("insert")
    insert.add_argument("--key", required=True)
    insert.add_argument("--failpoint", choices=sorted(REUSE_FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--key", action="append", default=[])
    sub.add_parser("recover")

    args = parser.parse_args()
    store = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(args.file)

    if args.command == "evacuate":
        store.evacuate_middle_live_retirement_arena_tail_step(failpoint=killer(args.failpoint))
        raise AssertionError("requested v0.42 evacuation failpoint was not reached")

    if args.command == "insert":
        store.insert(args.key, failpoint=killer(args.failpoint))
        raise AssertionError("requested v0.42 insert failpoint was not reached")

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
