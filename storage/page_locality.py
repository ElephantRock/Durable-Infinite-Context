from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class BTreeStats:
    height: int
    total_pages: int
    internal_pages: int
    leaf_pages: int
    overflow_pages: int
    max_payload: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _path_depth(path: str) -> int:
    return 1 if path == "/" else max(1, path.count("/"))


def btree_stats(conn: sqlite3.Connection, name: str) -> BTreeStats:
    rows = conn.execute(
        "SELECT path,pagetype,mx_payload FROM dbstat WHERE name=?",
        (name,),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"dbstat returned no rows for {name}")

    height = 1
    internal_pages = 0
    leaf_pages = 0
    overflow_pages = 0
    max_payload = 0
    for path, page_type, payload in rows:
        page_type = str(page_type)
        if page_type == "internal":
            internal_pages += 1
        elif page_type == "leaf":
            leaf_pages += 1
        elif page_type == "overflow":
            overflow_pages += 1
        height = max(height, _path_depth(str(path)))
        max_payload = max(max_payload, int(payload or 0))

    return BTreeStats(
        height=height,
        total_pages=len(rows),
        internal_pages=internal_pages,
        leaf_pages=leaf_pages,
        overflow_pages=overflow_pages,
        max_payload=max_payload,
    )


def subject_shard(subject: str, shard_count: int) -> int:
    digest = hashlib.sha256(subject.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def _uses_index(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[str, str],
    index_name: str,
) -> bool:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    detail = " ".join(str(row[3]).lower() for row in rows)
    return index_name.lower() in detail and "scan" not in detail


def _point_lookup(
    conn: sqlite3.Connection,
    sql: str,
    subject: str,
) -> str | None:
    row = conn.execute(sql, (subject, "deadline")).fetchone()
    return None if row is None else str(row[0])


def run_fixed_shard_geometry(
    checkpoints: Iterable[int] = (1_000, 10_000, 50_000, 250_000, 1_000_000),
    shard_count: int = 64,
    page_size: int = 4096,
) -> dict:
    """Measure global-vs-fixed-shard B-tree page-path geometry.

    For these deliberately small non-overflow keys, ``height`` is the number of
    index B-tree pages on one root-to-leaf search path. It is therefore a defensible
    cold-cache index-page traversal count for a successful point lookup, excluding
    schema lookup, pager bookkeeping, filesystem metadata, device reads, and cache
    effects outside the index itself.
    """

    checkpoints = list(checkpoints)
    if not checkpoints or checkpoints != sorted(checkpoints) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive and increasing")
    if shard_count <= 0:
        raise ValueError("shard_count must be positive")

    fd, raw_path = tempfile.mkstemp(prefix="dic-page-locality-", suffix=".sqlite")
    os.close(fd)
    path = Path(raw_path)
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute(f"PRAGMA page_size={int(page_size)}")
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA temp_store=MEMORY")
            observed_page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            if observed_page_size != page_size:
                raise AssertionError((page_size, observed_page_size))

            conn.execute(
                "CREATE TABLE global_membership("
                "subject_id TEXT NOT NULL,predicate TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX idx_global_membership "
                "ON global_membership(subject_id,predicate)"
            )
            for shard in range(shard_count):
                conn.execute(
                    f"CREATE TABLE membership_shard_{shard}("
                    "subject_id TEXT NOT NULL,predicate TEXT NOT NULL)"
                )
                conn.execute(
                    f"CREATE INDEX idx_membership_shard_{shard} "
                    f"ON membership_shard_{shard}(subject_id,predicate)"
                )
            conn.commit()

            rows_out: list[dict] = []
            previous = 0
            for checkpoint in checkpoints:
                global_batch: list[tuple[str, str]] = []
                shard_batches: list[list[tuple[str, str]]] = [
                    [] for _ in range(shard_count)
                ]
                for index in range(previous, checkpoint):
                    subject = f"entity_{index:09d}"
                    item = (subject, "deadline")
                    global_batch.append(item)
                    shard_batches[subject_shard(subject, shard_count)].append(item)

                conn.executemany(
                    "INSERT INTO global_membership(subject_id,predicate) VALUES (?,?)",
                    global_batch,
                )
                for shard, batch in enumerate(shard_batches):
                    if batch:
                        conn.executemany(
                            f"INSERT INTO membership_shard_{shard}(subject_id,predicate) "
                            "VALUES (?,?)",
                            batch,
                        )
                conn.commit()

                global_stats = btree_stats(conn, "idx_global_membership")
                shard_stats = [
                    btree_stats(conn, f"idx_membership_shard_{shard}")
                    for shard in range(shard_count)
                ]
                occupancies = [
                    int(
                        conn.execute(
                            f"SELECT COUNT(*) FROM membership_shard_{shard}"
                        ).fetchone()[0]
                    )
                    for shard in range(shard_count)
                ]

                target_subject = f"entity_{checkpoint - 1:09d}"
                target_shard = subject_shard(target_subject, shard_count)
                target_index = f"idx_membership_shard_{target_shard}"
                global_sql = (
                    "SELECT predicate FROM global_membership "
                    "INDEXED BY idx_global_membership "
                    "WHERE subject_id=? AND predicate=?"
                )
                shard_sql = (
                    f"SELECT predicate FROM membership_shard_{target_shard} "
                    f"INDEXED BY {target_index} "
                    "WHERE subject_id=? AND predicate=?"
                )

                if _point_lookup(conn, global_sql, target_subject) != "deadline":
                    raise AssertionError("global point lookup failed")
                if _point_lookup(conn, shard_sql, target_subject) != "deadline":
                    raise AssertionError("sharded point lookup failed")
                if not _uses_index(
                    conn,
                    global_sql,
                    (target_subject, "deadline"),
                    "idx_global_membership",
                ):
                    raise AssertionError("global lookup did not use the intended index")
                if not _uses_index(
                    conn,
                    shard_sql,
                    (target_subject, "deadline"),
                    target_index,
                ):
                    raise AssertionError("sharded lookup did not use the intended index")
                if global_stats.overflow_pages != 0 or any(
                    stat.overflow_pages != 0 for stat in shard_stats
                ):
                    raise AssertionError(
                        "small membership keys unexpectedly used overflow pages"
                    )

                rows_out.append(
                    {
                        "membership_rows": checkpoint,
                        "page_size": page_size,
                        "global_height": global_stats.height,
                        "global_cold_index_pages": global_stats.height,
                        "global_index_pages": global_stats.total_pages,
                        "global_internal_pages": global_stats.internal_pages,
                        "global_leaf_pages": global_stats.leaf_pages,
                        "shard_count": shard_count,
                        "target_shard": target_shard,
                        "target_shard_rows": occupancies[target_shard],
                        "target_shard_height": shard_stats[target_shard].height,
                        "target_shard_cold_index_pages": shard_stats[target_shard].height,
                        "max_shard_rows": max(occupancies),
                        "max_shard_height": max(stat.height for stat in shard_stats),
                        "min_shard_height": min(stat.height for stat in shard_stats),
                        "max_shard_index_pages": max(
                            stat.total_pages for stat in shard_stats
                        ),
                    }
                )
                previous = checkpoint

            return {
                "page_size": page_size,
                "shard_count": shard_count,
                "checkpoints": checkpoints,
                "rows": rows_out,
                "measurement_scope": (
                    "dbstat B-tree root-to-leaf height for small non-overflow index "
                    "keys; counts index pages on the search path but excludes "
                    "sqlite_schema, pager bookkeeping, filesystem metadata, "
                    "storage-device reads, and cache hits"
                ),
            }
        finally:
            conn.close()
    finally:
        path.unlink(missing_ok=True)
