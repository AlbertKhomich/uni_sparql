import json
import argparse
from typing import Dict, Any

from sblp_enrich.cache_db import SqliteTableCache

def migrate_json_dict_to_sqlite(
    json_path: str, 
    sqlite_path: str, 
    table: str,
    batch: int = 2000,
    compress: bool = True,
) -> None:
    cache = SqliteTableCache(sqlite_path, table=table, compress=compress)

    with open(json_path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)

    i = 0
    for k, v in data.items():
        cache.set(k, v)
        i += 1
        if i % batch == 0:
            cache.commit()
            print(f"Inserted {i}...")

    cache.commit()
    cache.close()
    print(f"Done. Inserted {i} rows into {sqlite_path}")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--no-compress", action="store_true")
    args = ap.parse_args()

    migrate_json_dict_to_sqlite(
        json_path=args.json,
        sqlite_path=args.db,
        table=args.table,
        batch=args.batch,
        compress=not args.no_compress,
    )

if __name__ == "__main__":
    main()


