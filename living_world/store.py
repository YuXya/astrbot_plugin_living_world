"""Transactional JSON storage with persistent execution claims."""

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS objects (namespace TEXT, key TEXT, value TEXT NOT NULL, updated REAL, PRIMARY KEY(namespace,key))"
        )
        self.db.commit()

    def get(self, namespace, key, default=None):
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM objects WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, namespace, key, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (namespace, key, data, time.time()),
            )

    def claim(self, namespace, key, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO objects VALUES (?,?,?,?)",
                (namespace, key, data, time.time()),
            )
            return cursor.rowcount == 1

    def put_many(self, namespace, records):
        rows = [
            (namespace, key, json.dumps(value, ensure_ascii=False, allow_nan=False), time.time())
            for key, value in records
        ]
        with self.lock, self.db:
            self.db.executemany(
                "INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                rows,
            )

    def list(self, namespace):
        with self.lock:
            rows = self.db.execute(
                "SELECT value FROM objects WHERE namespace=? ORDER BY updated DESC", (namespace,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, namespace, key):
        with self.lock, self.db:
            self.db.execute("DELETE FROM objects WHERE namespace=? AND key=?", (namespace, key))

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
        with self.lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO objects VALUES (?,?,?,?)", rows)

    def close(self):
        with self.lock:
            self.db.close()
