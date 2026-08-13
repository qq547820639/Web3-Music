import hashlib, os, pathlib, time
import psycopg2

url=os.environ["DATABASE_URL"]
root=pathlib.Path(os.getenv("MIGRATIONS_DIR","/migrations"))
for attempt in range(60):
    try:
        conn=psycopg2.connect(url); break
    except Exception:
        if attempt==59: raise
        time.sleep(1)
conn.autocommit=False
with conn.cursor() as cur:
    cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY,checksum char(64) NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())")
conn.commit()
for path in sorted(root.glob("*.sql")):
    sql=path.read_text(encoding="utf-8")
    checksum=hashlib.sha256(sql.encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT checksum FROM schema_migrations WHERE version=%s",(path.name,)); row=cur.fetchone()
        if row:
            if row[0]!=checksum: raise RuntimeError(f"migration checksum changed: {path.name}")
            print(f"skip {path.name}",flush=True); continue
        print(f"apply {path.name}",flush=True)
        cur.execute(sql)
        cur.execute("INSERT INTO schema_migrations(version,checksum) VALUES(%s,%s)",(path.name,checksum))
    conn.commit()
print("migrations complete",flush=True)
