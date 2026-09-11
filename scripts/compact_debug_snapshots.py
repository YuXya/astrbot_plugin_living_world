"""Explicit maintenance of redundant diagnostic snapshots; no host history is accessed."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from living_world.debug_payload import compact_record  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    path = options.database.resolve(strict=True)
    connection = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if options.apply else "?mode=ro"), uri=True
    )
    connection.execute("PRAGMA busy_timeout=2000")
    headers = connection.execute(
        "SELECT key,updated FROM objects WHERE namespace='debug_records'"
    ).fetchall()
    count = before = after = skipped = 0
    for key, updated in headers:
        stored = connection.execute(
            "SELECT value FROM objects WHERE namespace='debug_records' AND key=? AND updated=?",
            (key, updated),
        ).fetchone()
        if stored is None:
            skipped += 1
            continue
        record = json.loads(stored[0])
        compact = compact_record(record)
        if compact == record:
            continue
        encoded = json.dumps(compact, ensure_ascii=False, allow_nan=False)
        if options.apply:
            with connection:
                cursor = connection.execute(
                    "UPDATE objects SET value=? WHERE namespace='debug_records' AND key=? AND updated=?",
                    (encoded, key, updated),
                )
                if cursor.rowcount == 0:
                    skipped += 1
                    continue
        count += 1
        before += len(stored[0].encode("utf-8"))
        after += len(encoded.encode("utf-8"))
    connection.close()
    print(
        json.dumps(
            {
                "mode": "applied" if options.apply else "preview",
                "database": str(path),
                "changed_records": count,
                "before_bytes": before,
                "after_bytes": after,
                "saved_bytes": before - after,
                "concurrent_records_skipped": skipped,
                "preserved": "Captured HTTP bodies, host history and all non-debug namespaces",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
