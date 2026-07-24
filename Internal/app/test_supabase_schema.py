#!/usr/bin/env python3
"""Static security contract for the optional Supabase Data API schema."""

import os
import re


TABLES = (
    "user_settings",
    "user_stats",
    "user_history",
    "user_reader_library",
    "user_favorites",
    "user_presets",
)


def test_all_exposed_tables_enforce_owner_rls():
    path = os.path.join(os.path.dirname(__file__), "cloud_schema.sql")
    sql = open(path, encoding="utf-8").read().lower()

    for table in TABLES:
        assert f"alter table public.{table} enable row level security" in sql
        assert re.search(
            rf'create policy "owner select" on public\.{table}\s+'
            rf'for select to authenticated\s+'
            rf'using \(\(select auth\.uid\(\)\) = user_id\)', sql)
        assert re.search(
            rf'create policy "owner insert" on public\.{table}\s+'
            rf'for insert to authenticated\s+'
            rf'with check \(\(select auth\.uid\(\)\) = user_id\)', sql)
        assert re.search(
            rf'create policy "owner update" on public\.{table}\s+'
            rf'for update to authenticated\s+'
            rf'using \(\(select auth\.uid\(\)\) = user_id\)\s+'
            rf'with check \(\(select auth\.uid\(\)\) = user_id\)', sql)
        assert re.search(
            rf'create policy "owner delete" on public\.{table}\s+'
            rf'for delete to authenticated\s+'
            rf'using \(\(select auth\.uid\(\)\) = user_id\)', sql)
        assert (f"grant select, insert, update, delete on public.{table} "
                "to authenticated") in sql

    assert " to anon" not in sql
    assert "security definer" not in sql
    assert "user_metadata" not in sql


if __name__ == "__main__":
    try:
        test_all_exposed_tables_enforce_owner_rls()
        print("  ok  test_all_exposed_tables_enforce_owner_rls")
    except Exception as exc:
        print("  FAIL test_all_exposed_tables_enforce_owner_rls: " + repr(exc))
        raise SystemExit(1)
