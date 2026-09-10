from __future__ import annotations

import argparse
import json
import os
import signal

from storage.scrubbing_reclaiming_radix_primary import (
    ScrubbingReclaimingRadixPrimaryStore,
)

RECLAIM_FAILPOINTS = {
    "mapping_unlinked",
    "free_header_written",
    "dependencies_synced",
    "committed",
}
REUSE_FAILPOINTS = {
    "data_scrubbed",
    "mapping_written",
    "owner_header_written",
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

    reclaim = sub.add_parser("crash-reclaim")
    reclaim.add_argument("--budget", type=int, required=True)
    reclaim.add_argument("--failpoint", choices=sorted(RECLAIM_FAILPOINTS), required=True)

    reuse = sub.add_parser("crash-reuse")
    reuse.add_argument("--segment-id", type=int, required=True)
    reuse.add_argument("--failpoint", choices=sorted(REUSE_FAILPOINTS), required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--segment-id", type=int, action="append", default=[])

    sub.add_parser("recover")

    args = parser.parse_args()
    store = ScrubbingReclaimingRadixPrimaryStore(args.file)

    def failpoint(stage: str) -> None:
        if stage == args.failpoint:
            abrupt_kill()

    if args.command == "crash-reclaim":
        store.reclaim_step(budget=args.budget, failpoint=failpoint)
        raise AssertionError("requested reclaim failpoint was not reached")

    if args.command == "crash-reuse":
        store.reuse_one_mapping(
            args.segment_id,
            failpoint=failpoint,
        )
        raise AssertionError("requested reuse failpoint was not reached")

    if args.command == "inspect":
        print(json.dumps(store.reclamation_snapshot(args.segment_id), sort_keys=True))
        return

    if args.command == "recover":
        print(json.dumps(store.recover(), sort_keys=True))
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
