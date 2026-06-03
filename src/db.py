from __future__ import annotations

import sqlite3
from pathlib import Path

from .auth import hash_password, verify_password
from .recommender import Movie


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watched_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                """
            )
            self._ensure_column(conn, "watched_items", "user_rating", "REAL")
            self._ensure_column(
                conn,
                "watched_items",
                "in_watched_history",
                "INTEGER NOT NULL DEFAULT 1",
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                """
            )

    def _ensure_column(
        self, conn: sqlite3.Connection, table_name: str, column_name: str, column_definition: str
    ) -> None:
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(column["name"] == column_name for column in columns):
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

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
        except sqlite3.IntegrityError:
            return False, "Username already exists."
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
            row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["username"] if row else None

    def add_watched_item(self, user_id: int, movie: Movie) -> None:
        import json

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
                SELECT user_rating
                FROM watched_items
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
                    UPDATE watched_items
                    SET in_watched_history = 0
                    WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                    """,
                    (user_id, tmdb_id, content_type),
                )

    def list_watched_items(self, user_id: int) -> list[Movie]:
        import json

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

    def set_user_rating(
        self,
        user_id: int,
        movie: Movie,
        user_rating: float,
    ) -> None:
        import json

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
                SELECT in_watched_history
                FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
            if not row:
                return
            if int(row["in_watched_history"] or 0) == 1:
                conn.execute(
                    """
                    UPDATE watched_items
                    SET user_rating = NULL
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
                SELECT user_rating
                FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
        if not row or row["user_rating"] is None:
            return None
        return float(row["user_rating"])

    def is_in_watched_history(self, user_id: int, tmdb_id: int, content_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT in_watched_history
                FROM watched_items
                WHERE user_id = ? AND tmdb_id = ? AND content_type = ?
                """,
                (user_id, tmdb_id, content_type),
            ).fetchone()
        if not row:
            return False
        return bool(int(row["in_watched_history"] or 0))

    def add_watchlist_item(self, user_id: int, movie: Movie) -> None:
        import json

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

    # ── Chat history ──────────────────────────────────────────────────────────

    def save_chat_message(self, user_id: int, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )

    def get_chat_history(self, user_id: int, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM chat_messages
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (user_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def clear_chat_history(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))

    def list_watchlist_items(self, user_id: int) -> list[Movie]:
        import json

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
