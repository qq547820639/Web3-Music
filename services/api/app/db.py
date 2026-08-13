import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from .settings import settings


_pool = None
_pool_slots = None
_pool_lock = threading.Lock()


def _connection_kwargs():
    return {
        "dsn": settings.database_url,
        "connect_timeout": settings.db_connect_timeout_seconds,
        "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        "application_name": "resonance-api",
    }


def pool():
    global _pool, _pool_slots
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg2.pool import ThreadedConnectionPool
                _pool = ThreadedConnectionPool(
                    settings.db_pool_min,
                    settings.db_pool_max,
                    **_connection_kwargs(),
                )
                _pool_slots = threading.BoundedSemaphore(settings.db_pool_max)
    return _pool


@contextmanager
def connection():
    p = pool()
    slots = _pool_slots
    if slots is None or not slots.acquire(timeout=settings.db_pool_acquire_timeout_seconds):
        raise psycopg2.OperationalError("database pool acquisition timed out")
    conn = None
    try:
        conn = p.getconn()
    except Exception:
        slots.release()
        raise
    broken = False
    try:
        if conn.closed:
            broken = True
            raise psycopg2.InterfaceError("pooled database connection is closed")
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        try:
            if not conn.closed:
                conn.rollback()  # also clears transaction-local tenant context
        except Exception:
            broken = True
        try:
            p.putconn(conn, close=broken or bool(conn.closed))
        finally:
            slots.release()


def close_pool():
    global _pool, _pool_slots
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            _pool_slots = None


def wait_for_db():
    last = None
    for _ in range(60):
        try:
            with connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"database unavailable: {last}")


def set_tenant(cur, workspace_id: str | None):
    if workspace_id:
        cur.execute("SELECT set_config('app.workspace_id',%s,true)", (str(workspace_id),))


@contextmanager
def transaction(workspace_id: str | None = None):
    with connection() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                set_tenant(cur, workspace_id)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def fetch_one(sql, params=(), workspace_id: str | None = None):
    with connection() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        set_tenant(cur, workspace_id)
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(sql, params=(), workspace_id: str | None = None):
    with connection() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        set_tenant(cur, workspace_id)
        cur.execute(sql, params)
        return cur.fetchall()
