from __future__ import annotations

import argparse
import json

from recyclable_retirement_descriptor_worker import abrupt_kill
from storage.trimmable_retirement_descriptor_primary import (
    TrimmableRetirementDescriptorPrimaryStore,
)

INSERT_FAILPOINTS = {
    "allocated",
    "reused_extent_scrubbed",
    "retirement_descriptor_written",
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "pages_written",
    "data_synced",
    "committed",
}
TRIM_FAILPOINTS = {
    "descriptor_tail_release_selected",
    "committed",
    "descriptor_tail_truncated",
    "trim_synced",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    insert = sub.add_parser("insert")
    insert.add_argument("--key", required=True)
    insert.add_argument("--failpoint", choices=sorted(INSERT_FAILPOINTS), required=True)

    trim = sub.add_parser("trim")
    trim.add_argument("--failpoint", choices=sorted(TRIM_FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--key", action="append", default=[])
    sub.add_parser("recover")

    args = parser.parse_args()
    store = TrimmableRetirementDescriptorPrimaryStore(args.file)

    if args.command == "insert":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()

        store.insert(args.key, failpoint=failpoint)
        raise AssertionError("requested insert failpoint was not reached")

    if args.command == "trim":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()

        store.trim_retirement_descriptor_tail_step(failpoint=failpoint)
        raise AssertionError("requested trim failpoint was not reached")

    if args.command == "inspect":
        print(
            json.dumps(
                {
                    "queue": store.retirement_queue_snapshot(),
                    "state": store.committed_state(tuple(args.key)),
                    "meta": store.meta_snapshot(),
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
