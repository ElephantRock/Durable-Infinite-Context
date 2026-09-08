from __future__ import annotations

import argparse
import json
import os
import signal

from storage.fixed_page_primary import FixedPagePrimaryStore

FAILPOINTS = {"pages_written", "data_synced", "committed"}


def abrupt_kill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    crash = sub.add_parser("crash")
    crash.add_argument("--key", required=True)
    crash.add_argument("--failpoint", choices=sorted(FAILPOINTS), required=True)

    lookup = sub.add_parser("lookup")
    lookup.add_argument("--key", required=True)

    sub.add_parser("recover")
    sub.add_parser("inspect")

    args = parser.parse_args()
    store = FixedPagePrimaryStore(args.file)

    if args.command == "crash":
        def failpoint(stage: str) -> None:
            if stage == args.failpoint:
                abrupt_kill()
        store.insert(args.key, failpoint=failpoint)
        raise AssertionError("requested failpoint was not reached")

    if args.command == "lookup":
        print(json.dumps(store.lookup(args.key).to_dict(), sort_keys=True))
        return

    if args.command == "recover":
        print(json.dumps(store.recover(), sort_keys=True))
        return

    if args.command == "inspect":
        print(
            json.dumps(
                {
                    "meta": store.meta_snapshot(),
                    "audit": store.audit(),
                    "address_formula": store.address_formula(),
                },
                sort_keys=True,
            )
        )
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
