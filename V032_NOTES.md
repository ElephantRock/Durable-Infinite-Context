# v0.32 — Retired-generation reclamation

Initial target: bound reclamation work by an explicit per-step budget rather than generation capacity or total retired history, reuse reclaimed physical extents without resurrecting retired mappings or payloads, and preserve a single committed interpretation under process death.

The first implementation intentionally keeps lifecycle ownership explicit and separate from automatic primary-generation integration because existing logical generation boundaries are not guaranteed to be 16-page segment aligned. The experiment therefore tests mapping lifecycle first and treats full generation-boundary integration as a later requirement.
