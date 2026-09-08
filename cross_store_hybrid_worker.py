from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path

from storage.cross_store_hybrid import CrossStoreHybridStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", required=True)
    parser.add_argument("--overflow", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    crash = sub.add_parser("crash")
    crash.add_argument("--key", required=True)
    crash.add_argument("--failpoint", required=True)

    sub.add_parser("recover")
    sub.add_parser("snapshot")
    args = parser.parse_args()

    store = CrossStoreHybridStore(Path(args.primary), Path(args.overflow))
    if args.command == "crash":
        def kill(stage: str) -> None:
            if stage == args.failpoint:
                os.kill(os.getpid(), signal.SIGKILL)

        store.insert(args.key, failpoint=kill, recovery_first=False)
        raise AssertionError(f"requested failpoint was not reached: {args.failpoint}")
    if args.command == "recover":
        print(json.dumps(store.recover().to_dict(), sort_keys=True))
        return
    if args.command == "snapshot":
        print(json.dumps(store.logical_snapshot(), sort_keys=True))
        return
    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
