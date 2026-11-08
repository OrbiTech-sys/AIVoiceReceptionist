"""Copy this app's data from one database to another, e.g. from the local SQLite file to Supabase.

    .venv/Scripts/python copy_data.py "postgresql+psycopg://postgres.REF:PASSWORD@HOST:5432/postgres?sslmode=require"

The target must already have the tables (run `alembic upgrade head` against it first). Only tables that are empty in the
target are filled, so running it twice never duplicates anything, and the target's data is never deleted or changed.
By default the source is the database in VR_DATABASE_URL / backend/data/voicerep.db. Use --from to name another one,
and --tables to copy just some (parents first: "tenants knowledge_entries").
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, func, insert, inspect, select, text

from app import models  # noqa: F401  (registers every table)
from app.config import get_settings
from app.db import Base, make_engine


def fix_sequences(conn) -> list[str]:
    """PostgreSQL numbers new rows with a counter. Rows copied in with their own numbers don't move it, so the next new
    row would reuse a number and fail. Move each counter past the highest number in use."""
    fixed = []
    for table in Base.metadata.sorted_tables:
        for col in table.primary_key.columns:
            seq = conn.scalar(text("select pg_get_serial_sequence(:t, :c)"), {"t": f'public."{table.name}"', "c": col.name})
            if seq:
                conn.execute(text(f'select setval(:s, coalesce((select max("{col.name}") from public."{table.name}"), 1), '
                                  f'(select max("{col.name}") is not null from public."{table.name}"))'), {"s": seq})
                fixed.append(table.name)
    return fixed


def copy(source_url: str, target_url: str, only: list[str] | None = None, out=print) -> dict[str, int]:
    if source_url == target_url:
        raise SystemExit("The source and the target are the same database.")
    src, dst = make_engine(source_url), make_engine(target_url)
    copied: dict[str, int] = {}
    with src.connect() as s, dst.begin() as d:
        have_tables = set(inspect(s).get_table_names())  # an older source may lack the newest tables
        for table in Base.metadata.sorted_tables:  # parents before the tables that refer to them
            if (only and table.name not in only) or table.name not in have_tables:
                continue
            rows = [dict(r._mapping) for r in s.execute(select(table))]
            if not rows:
                continue
            have = d.scalar(select(func.count()).select_from(table))
            if have:
                out(f"  {table.name}: skipped, the target already has {have} rows")
                continue
            for i in range(0, len(rows), 500):
                d.execute(insert(table), rows[i:i + 500])
            copied[table.name] = len(rows)
            out(f"  {table.name}: copied {len(rows)} rows")
        if dst.dialect.name == "postgresql":
            for name in fix_sequences(d):
                out(f"  {name}: id counter moved past the copied rows")
    src.dispose()
    dst.dispose()
    return copied


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", help="address of the database to copy into")
    p.add_argument("--from", dest="source", default=get_settings().database_url, help="address of the database to copy from")
    p.add_argument("--tables", nargs="*", help="copy only these tables")
    a = p.parse_args()
    print(f"Copying into {create_engine(a.target).url.render_as_string(hide_password=True)}")
    copy(a.source, a.target, a.tables)
    print("Done.")


if __name__ == "__main__":
    sys.exit(main())
