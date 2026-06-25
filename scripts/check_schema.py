"""Verify the audio_tasks schema after running `alembic upgrade head`."""
import psycopg2

with psycopg2.connect(
    host="localhost", user="postgres", password="postgres123", dbname="music_ai"
) as c:
    with c.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'audio_tasks'
            ORDER BY ordinal_position
            """
        )
        print("columns:")
        for row in cur.fetchall():
            print(" ", row)

        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'audio_tasks'"
        )
        print("indexes:", [r[0] for r in cur.fetchall()])

        cur.execute(
            "SELECT version_num FROM alembic_version"
        )
        print("alembic version:", cur.fetchone()[0])
