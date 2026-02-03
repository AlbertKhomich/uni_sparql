import json, sqlite3, time, zlib
from typing import Any, Optional

class SqliteTableCache:
    def __init__(self, db_path: str, table: str, compress: bool = True):
        self.db_path = db_path
        self.table = table
        self.compress = compress
        self.db = sqlite3.connect(db_path, timeout=60)
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA synchronous=NORMAL;")
        self.db.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                k TEXT PRIMARY KEY,
                v BLOB NOT NULL,
                t INTEGER NOT NULL
            )
        """)
        self.db.commit()

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
        self.db.execute(
            f"INSERT INTO {self.table}(k, v, t) VALUES(?, ?, ?) "
            f"ON CONFLICT(k) DO UPDATE SET v=excluded.v, t=excluded.t",
            (key, blob, int(time.time()))
        )

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()