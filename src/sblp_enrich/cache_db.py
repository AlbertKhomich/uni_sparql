import json, sqlite3, time, zlib, random
from typing import Any, Optional

class SqliteTableCache:
    def __init__(self, db_path: str, table: str, compress: bool = True):
        self.db_path = db_path
        self.table = table
        self.compress = compress

        # isolation_level=None => autocommit; each statement is its own transaction
        self.db = sqlite3.connect(db_path, timeout=60, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA synchronous=NORMAL;")
        self.db.execute("PRAGMA busy_timeout=60000;")  # 60s wait on locks

        self.db.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                k TEXT PRIMARY KEY,
                v BLOB NOT NULL,
                t INTEGER NOT NULL
            )
        """)

    def get(self, key: str) -> Optional[Any]:
        cur = self.db.execute(f"SELECT v FROM {self.table} WHERE k=?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        blob = row[0]
        raw = zlib.decompress(blob) if self.compress else blob
        return json.loads(raw.decode("utf-8"))

    def set(self, key: str, value: Any) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        blob = zlib.compress(raw, 6) if self.compress else raw
        now = int(time.time())

        sql = (
            f"INSERT INTO {self.table}(k, v, t) VALUES(?, ?, ?) "
            f"ON CONFLICT(k) DO UPDATE SET v=excluded.v, t=excluded.t"
        )

        # Retry with backoff for bursts of concurrent writers
        for i in range(8):
            try:
                self.db.execute(sql, (key, blob, now))  # autocommit => lock held very briefly
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                time.sleep(min(2.0, 0.05 * (2 ** i)) + random.random() * 0.05)

        raise sqlite3.OperationalError("database is locked (after retries)")

    def commit(self) -> None:
        # In autocommit mode this is usually a no-op, but keep it for compatibility
        try:
            self.db.commit()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
