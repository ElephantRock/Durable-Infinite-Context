from __future__ import annotations

import argparse
import json
import os
import signal

from storage.durable_hybrid import DurableHybridStore


FAILPOINTS = {
    "route_uncommitted",
    "final_uncommitted",
    "committed",
}


def abrupt_kill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def crash(store: DurableHybridStore, key: str, failpoint: str) -> None:
    if failpoint == "route_uncommitted":
        conn, _trace = store.begin_insert_without_commit(key, stage="route")
        if not conn.in_transaction:
            raise AssertionError("route failpoint did not retain a live transaction")
        abrupt_kill()

    if failpoint == "final_uncommitted":
        conn, _trace = store.begin_insert_without_commit(key, stage="final")
        if not conn.in_transaction:
            raise AssertionError("final failpoint did not retain a live transaction")
        abrupt_kill()

    if failpoint == "committed":
        store.insert(key)
        abrupt_kill()

    raise ValueError(failpoint)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    crash_parser = sub.add_parser("crash")
    crash_parser.add_argument("--key", required=True)
    crash_parser.add_argument("--failpoint", choices=sorted(FAILPOINTS), required=True)

    lookup_parser = sub.add_parser("lookup")
    lookup_parser.add_argument("--key", required=True)

    sub.add_parser("recover")
    sub.add_parser("inspect")

    args = parser.parse_args()
    store = DurableHybridStore(args.db)

    if args.command == "crash":
        crash(store, args.key, args.failpoint)
        return

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
                    "snapshot": store.logical_snapshot(),
                    "settings": store.transaction_settings(),
                    "primary_bucket_lookup_uses_index": store.primary_bucket_lookup_uses_index(),
                    "overflow_lookup_uses_index": store.overflow_lookup_uses_index(),
                    "overflow_geometry": store.overflow_geometry(),
                    "logical_digest": store.logical_digest(),
                },
                sort_keys=True,
            )
        )
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
