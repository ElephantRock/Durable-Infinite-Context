from __future__ import annotations


def format_results(rows: list[dict]) -> str:
    cols = [
        "architecture",
        "total",
        "value_accuracy",
        "status_accuracy",
        "relation_accuracy",
        "provenance_accuracy",
        "exact_accuracy",
        "overclaim_rate",
    ]
    widths = {c: max(len(c), max(len(_fmt(r.get(c))) for r in rows)) for c in cols}
    line = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    body = []
    for r in rows:
        body.append(" | ".join(_fmt(r.get(c)).ljust(widths[c]) for c in cols))
    return "\n".join([line, sep] + body)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)
