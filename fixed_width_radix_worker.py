from __future__ import annotations

import argparse
import json
import os
import signal

from storage.fixed_width_radix_primary import FixedWidthRadixPrimaryStore

FAILPOINTS = {
    "allocated",
    "children_written",
    "parent_written",
    "dependencies_synced",
    "committed",
}


def abrupt_kill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    crash = sub.add_parser("crash-mapping")
    crash.add_argument("--segment-id", type=int, required=True)
    crash.add_argument("--failpoint", choices=sorted(FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--segment-id", type=int, action="append", default=[])

    sub.add_parser("recover")

    args = parser.parse_args()
    store = FixedWidthRadixPrimaryStore(args.file)

    if args.command == "crash-mapping":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()

        store.materialize_segment_mappings([args.segment_id], failpoint=failpoint)
        raise AssertionError("requested failpoint was not reached")

    if args.command == "inspect":
        print(json.dumps(store.mapping_snapshot(args.segment_id), sort_keys=True))
        return

    if args.command == "recover":
        print(json.dumps(store.recover(), sort_keys=True))
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
