from __future__ import annotations

import argparse
import json

from persistent_recovery_worker import FAILPOINTS, abrupt_kill
from storage.multi_intent import MultiIntentStore


def crash_active(store: MultiIntentStore, failpoint: str) -> None:
    """Kill a worker at a v0.9 transaction boundary for the active queued intent."""

    if failpoint not in FAILPOINTS:
        raise ValueError(f"unknown failpoint: {failpoint}")

    if failpoint == "prepared_committed":
        abrupt_kill()

    if failpoint == "canonical_uncommitted":
        open_transaction = store.begin_canonical_without_commit()
        if not open_transaction.in_transaction:
            raise AssertionError("canonical failpoint does not hold a live transaction")
        abrupt_kill()

    store.apply_canonical_transaction()
    if failpoint == "canonical_committed":
        abrupt_kill()

    if failpoint == "invalidation_uncommitted":
        open_transaction = store.begin_invalidation_without_commit()
        if not open_transaction.in_transaction:
            raise AssertionError("invalidation failpoint does not hold a live transaction")
        abrupt_kill()

    store.invalidate_transaction()
    if failpoint == "invalidated_committed":
        abrupt_kill()

    if failpoint == "partial_rebuild_uncommitted":
        open_transaction = store.begin_partial_rebuild_without_commit()
        if not open_transaction.in_transaction:
            raise AssertionError("partial rebuild failpoint does not hold a live transaction")
        abrupt_kill()

    store.partial_rebuild_transaction()
    if failpoint == "partial_rebuild_committed":
        abrupt_kill()

    if failpoint == "repair_uncommitted":
        open_transaction = store.begin_repair_without_commit()
        if not open_transaction.in_transaction:
            raise AssertionError("repair failpoint does not hold a live transaction")
        abrupt_kill()

    store.repair_transaction()
    if failpoint == "repaired_committed":
        abrupt_kill()

    if failpoint == "finalize_uncommitted":
        open_transaction = store.begin_finalize_without_commit()
        if not open_transaction.in_transaction:
            raise AssertionError("finalize failpoint does not hold a live transaction")
        abrupt_kill()

    store.finalize_transaction()
    if failpoint == "finalized_committed":
        abrupt_kill()

    raise AssertionError("crash-active command reached end without failpoint termination")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--entities", type=int, required=True)

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--operation", required=True)
    enqueue.add_argument("--index", type=int, required=True)
    enqueue.add_argument("--value", type=int, default=77)
    enqueue.add_argument("--writer")
    enqueue.add_argument("--hold-ms", type=int, default=0)

    sub.add_parser("promote")

    crash = sub.add_parser("crash-active")
    crash.add_argument("--failpoint", choices=sorted(FAILPOINTS), required=True)

    sub.add_parser("drain")
    sub.add_parser("inspect")

    read = sub.add_parser("read")
    read.add_argument("--subject", required=True)
    read.add_argument("--predicate", default="deadline")

    args = parser.parse_args()
    store = MultiIntentStore(args.db)

    if args.command == "bootstrap":
        store.bootstrap(args.entities)
        print(
            json.dumps(
                {
                    "settings": store.transaction_settings(),
                    "queue_lookup_uses_index": store.queue_lookup_uses_index(),
                    "dependency_lookup_uses_index": store.dependency_lookup_uses_index(),
                    "affected_traversal_uses_index": store.affected_traversal_uses_index(),
                    "clean": store.materialization_matches_clean_rebuild(),
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "enqueue":
        result = store.enqueue_operation(
            args.operation,
            args.index,
            new_value=args.value,
            writer=args.writer,
            hold_ms=args.hold_ms,
        )
        print(json.dumps(result, sort_keys=True))
        return

    if args.command == "promote":
        print(json.dumps(store.promote_next(), sort_keys=True))
        return

    if args.command == "crash-active":
        crash_active(store, args.failpoint)
        return

    if args.command == "drain":
        print(json.dumps(store.drain_all(), sort_keys=True))
        return

    if args.command == "inspect":
        print(
            json.dumps(
                {
                    "phase": store.phase_snapshot(),
                    "queue": store.queue_schema_snapshot(),
                    "settings": store.transaction_settings(),
                    "materialization_equal": store.materialization_matches_clean_rebuild(),
                    "all_derived_fresh": store.all_derived_fresh(),
                    "journal_empty": store.journal_empty(),
                    "full_rebuild_work": store.full_rebuild_work(),
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "read":
        blocked = False
        context = None
        try:
            context = store.read_context(args.subject, args.predicate)
        except RuntimeError:
            blocked = True
        print(json.dumps({"blocked": blocked, "context": context}, sort_keys=True))
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
