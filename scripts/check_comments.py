"""Verify Chinese comments on audio_tasks table, columns, enum, trigger."""
import psycopg2

with psycopg2.connect(
    host="localhost", user="postgres", password="postgres123", dbname="music_ai"
) as c:
    with c.cursor() as cur:
        # table
        cur.execute(
            "SELECT obj_description('audio_tasks'::regclass, 'pg_class')"
        )
        print("[TABLE]")
        print(" ", cur.fetchone()[0])

        # columns
        cur.execute(
            """
            SELECT a.attname, pg_catalog.col_description(a.attrelid, a.attnum)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'audio_tasks' AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        )
        print("\n[COLUMNS]")
        for name, comment in cur.fetchall():
            print(f"  {name:<14} {comment}")

        # enum
        cur.execute(
            """
            SELECT t.typname, pg_catalog.obj_description(t.oid, 'pg_type')
            FROM pg_type t WHERE t.typname = 'audio_task_status'
            """
        )
        print("\n[ENUM]")
        for n, c_ in cur.fetchall():
            print(f"  {n}  {c_}")

        # trigger function
        cur.execute(
            """
            SELECT p.proname, pg_catalog.obj_description(p.oid, 'pg_proc')
            FROM pg_proc p WHERE p.proname = 'trg_set_updated_at'
            """
        )
        print("\n[TRIGGER FUNCTION]")
        for n, c_ in cur.fetchall():
            print(f"  {n}  {c_}")

        # trigger
        cur.execute(
            """
            SELECT tgname, pg_catalog.obj_description(t.oid, 'pg_trigger')
            FROM pg_trigger t
            WHERE tgname = 'set_audio_tasks_updated_at'
            """
        )
        print("\n[TRIGGER]")
        for n, c_ in cur.fetchall():
            print(f"  {n}  {c_}")
