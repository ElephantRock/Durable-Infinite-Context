from __future__ import annotations

import argparse
import json
import os
import signal

from storage.process_store import PersistentProcessStore


FAILPOINTS = {
    "prepared_committed",
    "canonical_uncommitted",
    "canonical_committed",
    "invalidation_uncommitted",
    "invalidated_committed",
    "partial_rebuild_committed",
    "repaired_committed",
}


def abrupt_kill() -> None:
    """Terminate without Python cleanup so SQLite sees a real process death."""

    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("SIGKILL unexpectedly returned")


def crash(store: PersistentProcessStore, operation: str, index: int, failpoint: str) -> None:
    if failpoint not in FAILPOINTS:
        raise ValueError(f"unknown failpoint: {failpoint}")

    store.prepare_operation(operation, index)
    if failpoint == "prepared_committed":
        abrupt_kill()

    if failpoint == "canonical_uncommitted":
        # Leave a real SQLite write transaction open. SIGKILL must cause WAL
        # recovery to discard both the canonical write and its phase advance.
        store.begin_canonical_without_commit()
        abrupt_kill()

    store.apply_canonical_transaction()
    if failpoint == "canonical_committed":
        abrupt_kill()

    if failpoint == "invalidation_uncommitted":
        # Canonical transaction is durable, but invalidation + phase advance are
        # deliberately killed before their transaction commits.
        store.begin_invalidation_without_commit()
        abrupt_kill()

    store.invalidate_transaction()
    if failpoint == "invalidated_committed":
        abrupt_kill()

    store.partial_rebuild_transaction()
    if failpoint == "partial_rebuild_committed":
        abrupt_kill()

    store.repair_transaction()
    if failpoint == "repaired_committed":
        abrupt_kill()

    raise AssertionError("crash command reached end without failpoint termination")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--entities", type=int, required=True)

    mutate = sub.add_parser("crash")
    mutate.add_argument("--operation", required=True)
    mutate.add_argument("--index", type=int, required=True)
    mutate.add_argument("--failpoint", choices=sorted(FAILPOINTS), required=True)

    sub.add_parser("recover")
    sub.add_parser("inspect")

    args = parser.parse_args()
    store = PersistentProcessStore(args.db)

    if args.command == "bootstrap":
        store.bootstrap(args.entities)
        print(json.dumps({
            "settings": store.transaction_settings(),
            "dependency_lookup_uses_index": store.dependency_lookup_uses_index(),
            "clean": store.materialization_matches_clean_rebuild(),
            "full_rebuild_work": store.full_rebuild_work(),
        }, sort_keys=True))
        return

    if args.command == "crash":
        crash(store, args.operation, args.index, args.failpoint)
        return

    if args.command == "recover":
        trace = store.recover()
        print(json.dumps(trace.to_dict(), sort_keys=True))
        return

    if args.command == "inspect":
        print(json.dumps({
            "phase": store.phase_snapshot(),
            "settings": store.transaction_settings(),
            "all_derived_fresh": store.all_derived_fresh(),
            "journal_empty": store.journal_empty(),
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "full_rebuild_work": store.full_rebuild_work(),
        }, sort_keys=True))
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
