from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .auth import hash_password, verify_password
from .recommender import Movie

# Pooled Postgres connections idle longer than this are replaced rather than reused: Neon
# suspends the database after 5 idle minutes and the old connection may be dead.
PG_MAX_IDLE_SECONDS = 240


# ---------------------------------------------------------------------------
# PostgreSQL compatibility shim
# ---------------------------------------------------------------------------

class _PGConn:
    """
    Wraps a psycopg2 connection to expose the same
    ``execute() → fetchone() / fetchall()`` interface that sqlite3 uses,
    so the rest of Database needs zero branching.
    """

    def __init__(self, raw_conn) -> None:
        import psycopg2.extras
        self._raw = raw_conn
        self._cur = raw_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql: str, params: tuple = ()) -> "_PGConn":
        # psycopg2 uses %s placeholders; sqlite3 uses ?
        self._cur.execute(sql.replace("?", "%s"), params)
        return self

    def fetchone(self) -> dict | None:
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict]:
        return [dict(r) for r in self._cur.fetchall()]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    """
    Thin persistence layer.  Uses SQLite when DATABASE_URL is absent (local
    development) and PostgreSQL when DATABASE_URL is set (production).
    The public interface is identical in both modes.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._pg_url: str | None = os.environ.get("DATABASE_URL")
        self._pg_pool = None
        self._pg_last_used: dict[int, float] = {}
        self._pg_lock = threading.Lock()
        if not self._pg_url:
            self.db_path = str(db_path)
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── connection helpers ─────────────────────────────────────────────────────

    @contextmanager
    def _connect(self):
        """
        Context manager yielding a connection-like object.
        Commits on clean exit, rolls back on exception, always closes.
        """
        if self._pg_url:
            raw = self._pg_acquire()
            conn = _PGConn(raw)
            broken = False
            try:
                yield conn
                raw.commit()
            except Exception as exc:
                import psycopg2
                broken = isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))
                if not broken:
                    raw.rollback()
                raise
            finally:
                self._pg_release(raw, broken)
        else:
            raw = sqlite3.connect(self.db_path)
            raw.row_factory = sqlite3.Row
            try:
                yield raw
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()

    def _pg_acquire(self):
        """Take a pooled Postgres connection, re-checking ones idle long enough to have been dropped."""
        import psycopg2
        from psycopg2 import pool

        with self._pg_lock:
            if self._pg_pool is None:
                # The timeouts make a dead connection fail in seconds instead of hanging the
                # request: connect_timeout bounds opening one, tcp_user_timeout (ms) bounds a
                # query the server never acknowledges.
                self._pg_pool = pool.ThreadedConnectionPool(
                    1, 10, self._pg_url,
                    connect_timeout=10, tcp_user_timeout=10000,
                    keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                )
        raw = self._pg_pool.getconn()
        last_used = self._pg_last_used.get(id(raw))   # None: a connection just opened
        idle = time.time() - last_used if last_used is not None else 0
        if idle > PG_MAX_IDLE_SECONDS:
            # Neon suspends an idle database after 5 minutes, which can leave this connection
            # dead without either side noticing. Don't test it; open a fresh one.
            self._pg_discard(raw)
            raw = self._pg_pool.getconn()
        elif raw.closed or idle > 30:
            try:
                with raw.cursor() as cur:
                    cur.execute("SELECT 1")
                raw.rollback()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self._pg_discard(raw)
                raw = self._pg_pool.getconn()
        return raw

    def _pg_release(self, raw, broken: bool) -> None:
        self._pg_last_used[id(raw)] = time.time()
        self._pg_pool.putconn(raw, close=broken or raw.closed)
        if raw.closed:   # the pool also closes any beyond the one it keeps idle
            self._pg_last_used.pop(id(raw), None)

    def _pg_discard(self, raw) -> None:
        self._pg_last_used.pop(id(raw), None)
        self._pg_pool.putconn(raw, close=True)

    def _pk(self) -> str:
        """Auto-increment primary key DDL fragment."""
        return "SERIAL PRIMARY KEY" if self._pg_url else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # ── schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        pk = self._pk()
        with self._connect() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS users (
                    id {pk},
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._ensure_column(conn, "users", "region", "TEXT NOT NULL DEFAULT 'IN'")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS watched_items (
                    id {pk},
                    user_id INTEGER NOT NULL,
                    tmdb_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    overview TEXT NOT NULL DEFAULT '',
                    poster_url TEXT NOT NULL DEFAULT '',
                    backdrop_url TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT '',
                    year INTEGER NOT NULL DEFAULT 0,
                    rating REAL NOT NULL DEFAULT 0,
                    genres_json TEXT NOT NULL DEFAULT '[]',
                    mood_tags_json TEXT NOT NULL DEFAULT '[]',
                    watched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, tmdb_id, content_type),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)
            self._ensure_column(conn, "watched_items", "user_rating", "REAL")
            self._ensure_column(
                conn,
                "watched_items",
                "in_watched_history",
                "INTEGER NOT NULL DEFAULT 1",
            )
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id {pk},
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    recs_json TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)
            # Existing DBs predate recs_json. Checked first rather than tried and caught:
            # on Postgres a failed ALTER aborts the whole schema transaction.
            self._ensure_column(conn, "chat_messages", "recs_json", "TEXT")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id {pk},
                    user_id INTEGER NOT NULL,
                    tmdb_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    overview TEXT NOT NULL DEFAULT '',
                    poster_url TEXT NOT NULL DEFAULT '',
                    backdrop_url TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT '',
                    year INTEGER NOT NULL DEFAULT 0,
                    rating REAL NOT NULL DEFAULT 0,
                    genres_json TEXT NOT NULL DEFAULT '[]',
                    mood_tags_json TEXT NOT NULL DEFAULT '[]',
                    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, tmdb_id, content_type),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    # Allowlist of columns that _ensure_column may add.  Keeps f-string DDL safe.
    _ALLOWED_COLUMNS: dict[str, str] = {
        "user_rating": "REAL",
        "in_watched_history": "INTEGER NOT NULL DEFAULT 1",
        "region": "TEXT NOT NULL DEFAULT 'IN'",
        "recs_json": "TEXT",
    }

    def _ensure_column(
        self, conn, table_name: str, column_name: str, column_definition: str
    ) -> None:
        if column_name not in self._ALLOWED_COLUMNS:
            raise ValueError(f"_ensure_column: unknown column '{column_name}'")
        if self._pg_url:
            row = conn.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ? AND column_name = ?
                """,
                (table_name, column_name),
            ).fetchone()
            if row is not None:
                return
        else:
            # table_name is always a hardcoded string from _init_schema; safe to interpolate.
            cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()  # noqa: S608
            if any(c["name"] == column_name for c in cols):
                return
        conn.execute(  # noqa: S608
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )

    # ── users ─────────────────────────────────────────────────────────────────

    def create_user(self, username: str, password: str) -> tuple[bool, str]:
        clean_username = username.strip()
        if len(clean_username) < 3:
            return False, "Username must be at least 3 characters."
        if len(password) < 6:
            return False, "Password must be at least 6 characters."

        password_hash = hash_password(password)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (clean_username, password_hash),
                )
        except Exception as e:
            # sqlite3 → "UNIQUE constraint failed"
            # psycopg2 → "duplicate key value violates unique constraint"
            msg = str(e).lower()
            if "unique" in msg or "duplicate" in msg:
                return False, "Username already exists."
            raise
        return True, "Account created."

    def authenticate_user(self, username: str, password: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        if not row:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return int(row["id"])

    def get_username(self, user_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["username"] if row else None

    def get_user_region(self, user_id: int) -> str | None:
        """ISO 3166-1 country code the user picked for streaming availability."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT region FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["region"] if row else None

    def set_user_region(self, user_id: int, region: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET region = ? WHERE id = ?", (region, user_id))

    # ── watched history ───────────────────────────────────────────────────────

    def add_watched_item(self, user_id: int, movie: Movie) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watched_items (
                    user_id, tmdb_id, content_type, title, overview, poster_url, backdrop_url,
                    language, year, rating, genres_json, mood_tags_json, in_watched_history
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(user_id, tmdb_id, content_type) DO UPDATE SET
                    title = excluded.title,
                    overview = excluded.overview,
                    poster_url = excluded.poster_url,
                    backdrop_url = excluded.backdrop_url,
                    language = excluded.language,
                    year = excluded.year,
                    rating = excluded.rating,
                    genres_json = excluded.genres_json,
                    mood_tags_json = excluded.mood_tags_json,
                    in_watched_history = 1
                """,
                (
                    user_id,
                    movie.tmdb_id,
                    movie.content_type,
                    movie.title,
                    movie.overview,
                    movie.poster_url,
                    movie.backdrop_url,
                    movie.language,
                    movie.year,
                    movie.rating,
                    json.dumps(movie.genres),
                    json.dumps(movie.mood_tags),
                ),
            )

    def remove_watched_item(self, user_id: int, tmdb_id: int, content_type: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_rating FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
            if not row:
                return
            if row["user_rating"] is None:
                conn.execute(
                    "DELETE FROM watched_items WHERE user_id = ? AND tmdb_id = ? AND content_type = ?",
                    (user_id, tmdb_id, content_type),
                )
            else:
                conn.execute(
                    """
                    UPDATE watched_items SET in_watched_history = 0
                    WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                    """,
                    (user_id, tmdb_id, content_type),
                )

    def list_watched_items(self, user_id: int) -> list[Movie]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tmdb_id, content_type, title, overview, poster_url, backdrop_url,
                       language, year, rating, genres_json, mood_tags_json, user_rating
                FROM watched_items
                WHERE user_id = ? AND in_watched_history = 1
                ORDER BY watched_at DESC
                """,
                (user_id,),
            ).fetchall()

        items: list[Movie] = []
        for row in rows:
            items.append(
                Movie(
                    title=row["title"],
                    genres=json.loads(row["genres_json"] or "[]"),
                    mood_tags=json.loads(row["mood_tags_json"] or "[]"),
                    platforms=[],
                    language=row["language"] or "",
                    year=int(row["year"] or 0),
                    rating=float(row["rating"] or 0.0),
                    tmdb_id=int(row["tmdb_id"] or 0),
                    content_type=row["content_type"] or "movie",
                    overview=row["overview"] or "",
                    poster_url=row["poster_url"] or "",
                    backdrop_url=row["backdrop_url"] or "",
                    user_rating=(
                        float(row["user_rating"]) if row["user_rating"] is not None else None
                    ),
                )
            )
        return items

    # ── ratings ───────────────────────────────────────────────────────────────

    def set_user_rating(self, user_id: int, movie: Movie, user_rating: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watched_items (
                    user_id, tmdb_id, content_type, title, overview, poster_url, backdrop_url,
                    language, year, rating, genres_json, mood_tags_json, user_rating, in_watched_history
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(user_id, tmdb_id, content_type) DO UPDATE SET
                    title = excluded.title,
                    overview = excluded.overview,
                    poster_url = excluded.poster_url,
                    backdrop_url = excluded.backdrop_url,
                    language = excluded.language,
                    year = excluded.year,
                    rating = excluded.rating,
                    genres_json = excluded.genres_json,
                    mood_tags_json = excluded.mood_tags_json,
                    user_rating = excluded.user_rating
                """,
                (
                    user_id,
                    movie.tmdb_id,
                    movie.content_type,
                    movie.title,
                    movie.overview,
                    movie.poster_url,
                    movie.backdrop_url,
                    movie.language,
                    movie.year,
                    movie.rating,
                    json.dumps(movie.genres),
                    json.dumps(movie.mood_tags),
                    user_rating,
                ),
            )

    def remove_user_rating(self, user_id: int, tmdb_id: int, content_type: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT in_watched_history FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
            if not row:
                return
            if int(row["in_watched_history"] or 0) == 1:
                conn.execute(
                    """
                    UPDATE watched_items SET user_rating = NULL
                    WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                    """,
                    (user_id, tmdb_id, content_type),
                )
            else:
                conn.execute(
                    "DELETE FROM watched_items WHERE user_id = ? AND tmdb_id = ? AND content_type = ?",
                    (user_id, tmdb_id, content_type),
                )

    def get_user_rating(self, user_id: int, tmdb_id: int, content_type: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_rating FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
        if not row or row["user_rating"] is None:
            return None
        return float(row["user_rating"])

    def get_status_keys(self, user_id: int) -> tuple[set[tuple[int, str]], set[tuple[int, str]]]:
        """(watched, watchlist) sets of (tmdb_id, content_type), in two queries total."""
        with self._connect() as conn:
            watched_rows = conn.execute(
                "SELECT tmdb_id, content_type, in_watched_history FROM watched_items WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            watchlist_rows = conn.execute(
                "SELECT tmdb_id, content_type FROM watchlist_items WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        watched = {
            (int(r["tmdb_id"]), r["content_type"])
            for r in watched_rows
            if int(r["in_watched_history"] or 0)
        }
        watchlist = {(int(r["tmdb_id"]), r["content_type"]) for r in watchlist_rows}
        return watched, watchlist

    def get_title_state(
        self, user_id: int, tmdb_id: int, content_type: str
    ) -> tuple[float | None, bool, bool]:
        """(saved_rating, is_watched, in_watchlist) for one title using a single connection."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_rating, in_watched_history FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
            wl = conn.execute(
                "SELECT 1 FROM watchlist_items WHERE user_id = ? AND tmdb_id = ? AND content_type = ?",
                (user_id, tmdb_id, content_type),
            ).fetchone()
        rating = float(row["user_rating"]) if row and row["user_rating"] is not None else None
        watched = bool(int(row["in_watched_history"] or 0)) if row else False
        return rating, watched, wl is not None

    def is_in_watched_history(self, user_id: int, tmdb_id: int, content_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT in_watched_history FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
        if not row:
            return False
        return bool(int(row["in_watched_history"] or 0))

    # ── watchlist ─────────────────────────────────────────────────────────────

    def add_watchlist_item(self, user_id: int, movie: Movie) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist_items (
                    user_id, tmdb_id, content_type, title, overview, poster_url, backdrop_url,
                    language, year, rating, genres_json, mood_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, tmdb_id, content_type) DO NOTHING
                """,
                (
                    user_id,
                    movie.tmdb_id,
                    movie.content_type,
                    movie.title,
                    movie.overview,
                    movie.poster_url,
                    movie.backdrop_url,
                    movie.language,
                    movie.year,
                    movie.rating,
                    json.dumps(movie.genres),
                    json.dumps(movie.mood_tags),
                ),
            )

    def remove_watchlist_item(self, user_id: int, tmdb_id: int, content_type: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM watchlist_items WHERE user_id = ? AND tmdb_id = ? AND content_type = ?",
                (user_id, tmdb_id, content_type),
            )

    def is_in_watchlist(self, user_id: int, tmdb_id: int, content_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM watchlist_items WHERE user_id = ? AND tmdb_id = ? AND content_type = ?",
                (user_id, tmdb_id, content_type),
            ).fetchone()
        return row is not None

    def list_watchlist_items(self, user_id: int) -> list[Movie]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tmdb_id, content_type, title, overview, poster_url, backdrop_url,
                       language, year, rating, genres_json, mood_tags_json
                FROM watchlist_items
                WHERE user_id = ?
                ORDER BY added_at DESC
                """,
                (user_id,),
            ).fetchall()
        items: list[Movie] = []
        for row in rows:
            items.append(
                Movie(
                    title=row["title"],
                    genres=json.loads(row["genres_json"] or "[]"),
                    mood_tags=json.loads(row["mood_tags_json"] or "[]"),
                    platforms=[],
                    language=row["language"] or "",
                    year=int(row["year"] or 0),
                    rating=float(row["rating"] or 0.0),
                    tmdb_id=int(row["tmdb_id"] or 0),
                    content_type=row["content_type"] or "movie",
                    overview=row["overview"] or "",
                    poster_url=row["poster_url"] or "",
                    backdrop_url=row["backdrop_url"] or "",
                )
            )
        return items

    # ── chat history ──────────────────────────────────────────────────────────

    def save_chat_message(self, user_id: int, role: str, content: str, recs_json: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_messages (user_id, role, content, recs_json) VALUES (?, ?, ?, ?)",
                (user_id, role, content, recs_json),
            )

    def get_chat_history(self, user_id: int, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, recs_json FROM (
                    SELECT id, role, content, recs_json FROM chat_messages
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) sub ORDER BY sub.id ASC
                """,
                (user_id, limit),
            ).fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "recs_json": row["recs_json"],
            }
            for row in rows
        ]

    def clear_chat_history(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
