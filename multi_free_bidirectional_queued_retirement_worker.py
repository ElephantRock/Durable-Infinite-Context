from __future__ import annotations

import argparse
import os
import signal

from storage.multi_free_bidirectional_queued_retirement_descriptor_primary import (
    MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--failpoint", required=True)
    args = parser.parse_args()

    def crash(name: str) -> None:
        if name == args.failpoint:
            os.kill(os.getpid(), signal.SIGKILL)

    store = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(args.file)
    store.evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step(
        failpoint=crash
    )


if __name__ == "__main__":
    main()
