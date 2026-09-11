"""Transactional JSON storage with persistent execution claims."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._projections = {}
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS objects (namespace TEXT, key TEXT, value TEXT NOT NULL, updated REAL, PRIMARY KEY(namespace,key))"
        )
        self.db.commit()

    @contextmanager
    def transaction(self):
        """Serialize read-check-write operations, including across SQLite connections."""
        with self.lock:
            outer = not self.db.in_transaction
            if outer:
                self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                if outer:
                    self.db.rollback()
                raise
            else:
                if outer:
                    self.db.commit()

    def get(self, namespace, key, default=None):
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM objects WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, namespace, key, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.transaction():
            self.db.execute(
                "INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (namespace, key, data, time.time()),
            )
            self._invalidate_projection(namespace, key)

    def claim(self, namespace, key, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.transaction():
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO objects VALUES (?,?,?,?)",
                (namespace, key, data, time.time()),
            )
            if cursor.rowcount:
                self._invalidate_projection(namespace, key)
            return cursor.rowcount == 1

    def put_many(self, namespace, records):
        rows = [
            (namespace, key, json.dumps(value, ensure_ascii=False, allow_nan=False), time.time())
            for key, value in records
        ]
        with self.transaction():
            self.db.executemany(
                "INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                rows,
            )
            for _, key, _, _ in rows:
                self._invalidate_projection(namespace, key)

    def list(self, namespace):
        with self.lock:
            rows = self.db.execute(
                "SELECT value FROM objects WHERE namespace=? ORDER BY updated DESC,rowid DESC",
                (namespace,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _invalidate_projection(self, namespace, key):
        for (kind, _), cache in self._projections.items():
            if kind == namespace:
                cache.pop(key, None)

    def project(self, namespace, fields):
        """Read small metadata fields; reuse unchanged rows without loading their bodies."""
        fields = tuple(fields)
        if not fields or any(not field.replace("_", "").isalnum() for field in fields):
            raise ValueError("Projection fields must be simple identifiers")
        with self.lock:
            cache = self._projections.setdefault((namespace, fields), {})
            headers = self.db.execute(
                "SELECT key,updated FROM objects WHERE namespace=? ORDER BY updated DESC,rowid DESC",
                (namespace,),
            ).fetchall()
            changed = [
                key for key, updated in headers if key not in cache or cache[key][0] != updated
            ]
            expression = (
                "json_object("
                + ",".join(f"'{field}',json_extract(value,'$.{field}')" for field in fields)
                + ")"
            )
            for start in range(0, len(changed), 200):
                keys = changed[start : start + 200]
                marks = ",".join("?" for _ in keys)
                rows = self.db.execute(
                    f"SELECT key,updated,{expression} FROM objects WHERE namespace=? AND key IN ({marks})",
                    (namespace, *keys),
                ).fetchall()
                for key, updated, value in rows:
                    cache[key] = (
                        updated,
                        {k: v for k, v in json.loads(value).items() if v is not None},
                    )
            current = {key for key, _ in headers}
            for key in list(cache):
                if key not in current:
                    del cache[key]
            return [dict(cache[key][1]) for key, _ in headers]

    def apply_batch(self, writes, deletes=()):
        """Commit related writes and removals together, or leave every namespace unchanged."""
        deletes = list(deletes)
        rows = [
            (namespace, key, json.dumps(value, ensure_ascii=False, allow_nan=False), time.time())
            for namespace, key, value in writes
        ]
        with self.transaction():
            self.db.executemany("DELETE FROM objects WHERE namespace=? AND key=?", deletes)
            self.db.executemany(
                "INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(namespace,key) "
                "DO UPDATE SET value=excluded.value,updated=excluded.updated",
                rows,
            )
            for namespace, key in deletes:
                self._invalidate_projection(namespace, key)
            for namespace, key, _, _ in rows:
                self._invalidate_projection(namespace, key)

    def delete(self, namespace, key):
        with self.transaction():
            self.db.execute("DELETE FROM objects WHERE namespace=? AND key=?", (namespace, key))
            self._invalidate_projection(namespace, key)

    def export(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT namespace,key,value FROM objects WHERE namespace!='settings'"
            ).fetchall()
        return [{"namespace": n, "key": k, "value": json.loads(v)} for n, k, v in rows]

    @staticmethod
    def validate_records(records):
        if not isinstance(records, list) or len(records) > 100000:
            raise ValueError("备份记录无效或过大")
        rows = []
        for record in records:
            n, k, v = record["namespace"], record["key"], record["value"]
            if (
                not isinstance(n, str)
                or not isinstance(k, str)
                or n == "settings"
                or not isinstance(v, dict)
            ):
                raise ValueError("备份记录格式无效")
            rows.append((n, k, json.dumps(v, ensure_ascii=False, allow_nan=False), time.time()))
        return rows

    def restore(self, records):
        rows = self.validate_records(records)
        # Merge backups without forgetting newer execution claims or deliveries.
        with self.transaction():
            self.db.executemany("INSERT OR IGNORE INTO objects VALUES (?,?,?,?)", rows)
            self._projections.clear()

    def close(self):
        with self.lock:
            self.db.close()
