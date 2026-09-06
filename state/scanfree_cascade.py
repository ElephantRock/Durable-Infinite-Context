from __future__ import annotations

from state.cascade import CascadeMaintainer


class ScanFreeCascadeMaintainer(CascadeMaintainer):
    """Compatibility name for the now-canonical scan-free maintainer.

    v0.8 promoted scan-free affected-region discovery into ``CascadeMaintainer``.
    This subclass remains so the v0.7 correction verifier and older imports keep
    working without maintaining a second mutation implementation.
    """

    pass
