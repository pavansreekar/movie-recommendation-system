from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any
from flask import Flask, jsonify, request, session

from src.db import Database
from src.recommender import Movie, MovieRecommender, Recommendation
from src.tmdb_client import CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV, TMDBClient, build_movie_from_tmdb

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "app.db"
REGION = "IN"
PAGES_TO_SCAN = 5

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
FRONTEND_ORIGINS = {
    value.strip()
    for value in os.getenv(
        "FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if value.strip()
}

_db = Database(DB_PATH)
_tmdb_client: TMDBClient | None = None


def _normalize_provider_payload(raw_providers: dict[str, list[Any]] | None) -> dict[str, list[Any]]:
    raw_providers = raw_providers or {CONTENT_TYPE_MOVIE: [], CONTENT_TYPE_TV: []}
    normalized_providers: dict[str, list[Any]] = {}
    for ctype in [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]:
        providers = raw_providers.get(ctype, [])
        normalized: list[Any] = []
        for provider in providers:
            if isinstance(provider, dict):
                normalized.append(type("ProviderProxy", (), provider)())
            else:
                normalized.append(provider)
        normalized_providers[ctype] = normalized
    return normalized_providers


def get_db() -> Database:
    return _db


def get_tmdb() -> TMDBClient:
    global _tmdb_client
    if _tmdb_client is None:
        _tmdb_client = TMDBClient.from_env()
    return _tmdb_client


def api_login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return ("", 204)
        if not session.get("user_id"):
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        return func(*args, **kwargs)

    return wrapper


def content_label(content_type: str) -> str:
    return "Movie" if content_type == CONTENT_TYPE_MOVIE else "TV Show"


def tmdb_error_response(error: RuntimeError, status_code: int = 503):
    return jsonify({"ok": False, "error": str(error)}), status_code


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if origin in FRONTEND_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    return response


def fetch_dashboard_metadata(tmdb: TMDBClient) -> tuple[dict[str, dict[str, int]], dict[str, list[Any]], dict[str, str], str | None]:
    cached_genres = session.get("dashboard_genres_by_type") or {}
    cached_providers = session.get("dashboard_providers_by_type") or {}
    cached_languages = session.get("dashboard_languages") or {}

    genres_by_type = {
        ctype: cached_genres.get(ctype, {})
        for ctype in [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]
    }
    providers_by_type = _normalize_provider_payload(cached_providers)
    languages = cached_languages

    missing_tasks: dict[str, tuple[str, str | None]] = {}
    for ctype in [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]:
        if not genres_by_type[ctype]:
            missing_tasks[f"genres:{ctype}"] = ("genres", ctype)
        if not providers_by_type[ctype]:
            missing_tasks[f"providers:{ctype}"] = ("providers", ctype)
    if not languages:
        missing_tasks["languages"] = ("languages", None)

    if not missing_tasks:
        return genres_by_type, providers_by_type, languages, None

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(missing_tasks)) as executor:
        future_map = {}
        for task_type, content_type in missing_tasks.values():
            if task_type == "genres" and content_type:
                future_map[executor.submit(tmdb.get_genres, content_type)] = (task_type, content_type)
            elif task_type == "providers" and content_type:
                future_map[executor.submit(tmdb.get_providers, content_type, REGION, 3)] = (task_type, content_type)
            else:
                future_map[executor.submit(tmdb.get_languages)] = (task_type, content_type)

        for future in as_completed(future_map):
            task_type, content_type = future_map[future]
            try:
                result = future.result()
                if task_type == "genres" and content_type:
                    genres_by_type[content_type] = result
                elif task_type == "providers" and content_type:
                    providers_by_type[content_type] = result
                elif task_type == "languages":
                    languages = result
            except Exception as error:
                errors.append(str(error))

    session["dashboard_genres_by_type"] = genres_by_type
    session["dashboard_providers_by_type"] = {
        ctype: [provider.__dict__ for provider in providers_by_type.get(ctype, [])]
        for ctype in [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]
    }
    session["dashboard_languages"] = languages

    metadata_error = "; ".join(dict.fromkeys(errors)) if errors else None
    return genres_by_type, providers_by_type, languages, metadata_error


def discover_catalog(
    tmdb: TMDBClient,
    genre_names: list[str] | None,
    provider_name: str | None,
    language_code: str | None,
    genres_by_type: dict[str, dict[str, int]],
    providers_by_type: dict[str, list[Any]],
    content_types: list[str] | None = None,
) -> list[Movie]:
    catalog: list[Movie] = []
    provider_name_to_id_by_type = {
        ctype: {provider.provider_name: provider.provider_id for provider in providers}
        for ctype, providers in providers_by_type.items()
    }

    for content_type in (content_types or [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]):
        # Resolve genre names → IDs for this content type; pass as a list for OR-filtering.
        # If genres were explicitly requested but NONE resolve for this content type (e.g.
        # "Thriller" exists for movies but not TV), skip rather than fetching with no genre
        # filter — which would return all content of that type regardless of genre.
        genre_ids: list[int] | None = None
        if genre_names:
            resolved = [genres_by_type[content_type][g] for g in genre_names if g in genres_by_type[content_type]]
            if not resolved:
                continue          # genre doesn't exist for this content type → skip it
            genre_ids = resolved

        provider_id = provider_name_to_id_by_type[content_type].get(provider_name) if provider_name else None
        raw_items = tmdb.discover_content(
            content_type=content_type,
            genre_id=genre_ids,
            provider_id=provider_id,
            language_code=language_code,
            region=REGION,
            pages=PAGES_TO_SCAN,
        )
        genre_lookup = {gid: gname for gname, gid in genres_by_type[content_type].items()}
        catalog.extend(
            build_movie_from_tmdb(
                raw_item=item,
                genre_lookup=genre_lookup,
                selected_platform=provider_name,
                content_type=content_type,
            )
            for item in raw_items
        )
    return catalog


def filter_recommendations_against_watched(
    recommendations: list[Recommendation], watched_items: list[Movie]
) -> list[Recommendation]:
    watched_keys = {(item.content_type, item.tmdb_id) for item in watched_items}
    return [
        rec
        for rec in recommendations
        if (rec.movie.content_type, rec.movie.tmdb_id) not in watched_keys
    ]


def serialize_movie(movie: Movie) -> dict[str, Any]:
    return {
        "title": movie.title,
        "genres": movie.genres,
        "mood_tags": movie.mood_tags,
        "platforms": movie.platforms,
        "language": movie.language,
        "year": movie.year,
        "rating": movie.rating,
        "tmdb_id": movie.tmdb_id,
        "content_type": movie.content_type,
        "content_label": content_label(movie.content_type),
        "overview": movie.overview,
        "poster_url": movie.poster_url,
        "backdrop_url": movie.backdrop_url,
        "user_rating": movie.user_rating,
    }


def serialize_movie_for_user(db: Database, user_id: int, movie: Movie) -> dict[str, Any]:
    payload = serialize_movie(movie)
    payload["is_watched"] = db.is_in_watched_history(user_id, movie.tmdb_id, movie.content_type)
    return payload


def serialize_recommendation(rec: Recommendation) -> dict[str, Any]:
    return {
        "movie": serialize_movie(rec.movie),
        "score": rec.score,
        "reasons": rec.reasons,
    }


def get_dashboard_view(user_id: int) -> dict[str, Any]:
    tmdb = get_tmdb()
    db = get_db()
    filters = session.get("dashboard_filters", {})
    watched_from_db = db.list_watched_items(user_id)
    genres_by_type, providers_by_type, languages, metadata_error = fetch_dashboard_metadata(tmdb)

    provider_options = sorted(
        {
            provider.provider_name
            for providers in providers_by_type.values()
            for provider in providers
        }
    )
    genre_options = sorted({name for genres in genres_by_type.values() for name in genres.keys()})
    language_options = sorted(languages.keys())

    recommendations: list[Recommendation] = []
    recommendations_error: str | None = None

    # ── Catalog builder: deduplicates across platforms × languages ────────────
    def _build_catalog(genres_list, platforms_list, lang_codes, ctypes):
        """Build a deduplicated Movie catalog.
        lang_codes: list of ISO codes (e.g. ['en','hi']), or None/[] for any language.
        """
        codes = lang_codes if lang_codes else [None]
        plats = platforms_list if platforms_list else [None]
        seen: set[tuple] = set()
        merged: list[Movie] = []
        for lang in codes:
            for plat in plats:
                for movie in discover_catalog(
                    tmdb=tmdb, genre_names=genres_list, provider_name=plat,
                    language_code=lang, genres_by_type=genres_by_type,
                    providers_by_type=providers_by_type, content_types=ctypes,
                ):
                    key = (movie.content_type, movie.tmdb_id)
                    if key not in seen:
                        seen.add(key)
                        merged.append(movie)
        return merged

    if filters.get("active"):
        genres_filter: list[str] = filters.get("genres") or []
        platforms_filter: list[str] = filters.get("platforms") or []
        language_names: list[str] = filters.get("languages") or []
        language_codes: list[str] = [languages[l] for l in language_names if l in languages]
        content_type_filter = filters.get("content_type") or None
        content_types = [content_type_filter] if content_type_filter in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV) else None
        # Pass single language to recommender for hard-filter+score; None for multi/any
        rec_language = language_codes[0] if len(language_codes) == 1 else None
        try:
            catalog = _build_catalog(genres_filter or None, platforms_filter, language_codes, content_types)
            if not catalog:
                recommendations_error = "No titles found for these filters. Try relaxing filters."
            else:
                recommender = MovieRecommender(catalog)
                recommendations = recommender.recommend(
                    genres=genres_filter or None,
                    platform=None,
                    language=rec_language,
                    watched_history=watched_from_db,
                    top_k=None,
                )[:100]
                recommendations = filter_recommendations_against_watched(recommendations, watched_from_db)
                if not recommendations:
                    recommendations_error = "No recommendations found for these filters. Try relaxing some options."
        except RuntimeError as error:
            recommendations_error = f"TMDB discovery failed: {error}"

    # ── 3-tier smart fallbacks (always computed when filters are active) ────────
    # Previously gated behind len(recommendations) < 16, but now always built so
    # the frontend can reveal them on-demand via the "Show me more" button even
    # when there are plenty of primary results.
    FALLBACK_SIZE = 16  # fetch 16 so frontend can show 8 + keep 8 in reserve
    fallback_other_platforms: list[Recommendation] = []
    fallback_other_genres: list[Recommendation] = []
    fallback_other_languages: list[Recommendation] = []

    if filters.get("active"):
        excluded_keys: set[tuple[str, int]] = (
            {(m.content_type, m.tmdb_id) for m in watched_from_db}
            | {(r.movie.content_type, r.movie.tmdb_id) for r in recommendations}
        )

        # Fallback 1 — same genre + language, any platform
        if platforms_filter:
            try:
                cat1 = _build_catalog(genres_filter or None, [], language_codes, content_types)
                recs1 = MovieRecommender(cat1).recommend(
                    genres=genres_filter or None, platform=None, language=rec_language,
                    watched_history=watched_from_db, top_k=None,
                )
                fallback_other_platforms = [
                    r for r in recs1
                    if (r.movie.content_type, r.movie.tmdb_id) not in excluded_keys
                ][:FALLBACK_SIZE]
                excluded_keys |= {(r.movie.content_type, r.movie.tmdb_id) for r in fallback_other_platforms}
            except RuntimeError:
                pass

        # Fallback 2 — same platforms + language, any genre
        if genres_filter:
            try:
                cat2 = _build_catalog(None, platforms_filter, language_codes, content_types)
                recs2 = MovieRecommender(cat2).recommend(
                    genres=None, platform=None, language=rec_language,
                    watched_history=watched_from_db, top_k=None,
                )
                fallback_other_genres = [
                    r for r in recs2
                    if (r.movie.content_type, r.movie.tmdb_id) not in excluded_keys
                ][:FALLBACK_SIZE]
                excluded_keys |= {(r.movie.content_type, r.movie.tmdb_id) for r in fallback_other_genres}
            except RuntimeError:
                pass

        # Fallback 3 — same platforms + genre, other languages from target set
        selected_lang_set = set(language_codes)
        alt_langs = [l for l in ["en", "hi", "te", "ta"] if l not in selected_lang_set]
        if selected_lang_set and alt_langs:  # only if user chose specific language(s)
            try:
                cat3 = _build_catalog(genres_filter or None, platforms_filter, alt_langs, content_types)
                recs3 = MovieRecommender(cat3).recommend(
                    genres=genres_filter or None, platform=None, language=None,
                    watched_history=watched_from_db, top_k=None,
                )
                fallback_other_languages = [
                    r for r in recs3
                    if (r.movie.content_type, r.movie.tmdb_id) not in excluded_keys
                ][:FALLBACK_SIZE]
            except RuntimeError:
                pass

    return {
        "genre_options": genre_options,
        "provider_options": provider_options,
        "language_options": language_options,
        "watched_items": watched_from_db,
        "recommendations": recommendations[:40],
        "recommendations_error": recommendations_error,
        "fallback_other_platforms": fallback_other_platforms,
        "fallback_other_genres": fallback_other_genres,
        "fallback_other_languages": fallback_other_languages,
        "metadata_error": metadata_error,
        "filters": filters,
    }


@app.route("/api/session", methods=["GET", "OPTIONS"])
def api_session() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": True, "authenticated": False})
    return jsonify(
        {
            "ok": True,
            "authenticated": True,
            "user": {
                "id": int(user_id),
                "username": get_db().get_username(int(user_id)),
            },
        }
    )


@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def api_login() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "")
    password = payload.get("password", "")
    user_id = get_db().authenticate_user(username, password)
    if user_id is None:
        return jsonify({"ok": False, "error": "Invalid username or password."}), 401
    session.clear()
    session["user_id"] = user_id
    return jsonify(
        {
            "ok": True,
            "user": {"id": int(user_id), "username": get_db().get_username(int(user_id))},
        }
    )


@app.route("/api/auth/register", methods=["POST", "OPTIONS"])
def api_register() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    ok, message = get_db().create_user(payload.get("username", ""), payload.get("password", ""))
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)


@app.route("/api/auth/logout", methods=["POST", "OPTIONS"])
@api_login_required
def api_logout() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/dashboard", methods=["GET", "OPTIONS"])
@api_login_required
def api_dashboard() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    view = get_dashboard_view(int(session["user_id"]))
    return jsonify(
        {
            "ok": True,
            "genre_options": view["genre_options"],
            "provider_options": view["provider_options"],
            "language_options": view["language_options"],
            "watched_items": [serialize_movie(item) for item in view["watched_items"]],
            "recommendations": [serialize_recommendation(rec) for rec in view["recommendations"]],
            "recommendations_error": view["recommendations_error"],
            "fallback_other_platforms": [serialize_recommendation(rec) for rec in view["fallback_other_platforms"]],
            "fallback_other_genres": [serialize_recommendation(rec) for rec in view["fallback_other_genres"]],
            "fallback_other_languages": [serialize_recommendation(rec) for rec in view["fallback_other_languages"]],
            "metadata_error": view["metadata_error"],
            "filters": view["filters"],
        }
    )


@app.route("/api/dashboard/recommend", methods=["POST", "OPTIONS"])
@api_login_required
def api_dashboard_recommend() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    def _clean_str_list(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [p for p in raw if isinstance(p, str) and p.strip()]

    session["dashboard_filters"] = {
        "active": True,
        "genres": _clean_str_list(payload.get("genres")),
        "platforms": _clean_str_list(payload.get("platforms")),
        "languages": _clean_str_list(payload.get("languages")),
        "content_type": payload.get("content_type", "") or "",
    }
    return api_dashboard()


@app.route("/api/history", methods=["GET", "OPTIONS"])
@api_login_required
def api_history() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    watched_items = get_db().list_watched_items(int(session["user_id"]))
    return jsonify({"ok": True, "watched_items": [serialize_movie(item) for item in watched_items]})


@app.route("/api/watchlist", methods=["GET", "OPTIONS"])
@api_login_required
def api_watchlist() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    items = get_db().list_watchlist_items(int(session["user_id"]))
    return jsonify({"ok": True, "watchlist_items": [serialize_movie(item) for item in items]})


@app.route("/api/title/<content_type>/<int:tmdb_id>/watchlist", methods=["POST", "OPTIONS"])
@api_login_required
def api_toggle_watchlist(content_type: str, tmdb_id: int) -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    db = get_db()
    user_id = int(session["user_id"])
    if db.is_in_watchlist(user_id, tmdb_id, content_type):
        db.remove_watchlist_item(user_id, tmdb_id, content_type)
        return jsonify({"ok": True, "in_watchlist": False, "message": "Removed from watchlist."})
    try:
        details = get_tmdb().get_content_details(
            tmdb_id=tmdb_id, content_type=content_type, region=REGION
        )
        db.add_watchlist_item(user_id, details.movie)
        return jsonify({"ok": True, "in_watchlist": True, "message": f"Added '{details.movie.title}' to watchlist."})
    except RuntimeError as error:
        return tmdb_error_response(error)


@app.route("/api/title/<content_type>/<int:tmdb_id>", methods=["GET", "OPTIONS"])
@api_login_required
def api_title_detail(content_type: str, tmdb_id: int) -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    db = get_db()
    try:
        details = get_tmdb().get_content_details(
            tmdb_id=tmdb_id, content_type=content_type, region=REGION
        )
        movie = details.movie
        user_id = int(session["user_id"])
        return jsonify(
            {
                "ok": True,
                "movie": serialize_movie(movie),
                "genres": details.genres,
                "cast": details.cast,
                "providers": details.providers,
                "original_rating": details.original_rating,
                "saved_rating": db.get_user_rating(user_id, movie.tmdb_id, movie.content_type),
                "is_watched": db.is_in_watched_history(user_id, movie.tmdb_id, movie.content_type),
                "in_watchlist": db.is_in_watchlist(user_id, movie.tmdb_id, movie.content_type),
                "region": REGION,
            }
        )
    except RuntimeError as error:
        return tmdb_error_response(error)


@app.route("/api/title/<content_type>/<int:tmdb_id>/similar", methods=["GET", "OPTIONS"])
@api_login_required
def api_title_similar(content_type: str, tmdb_id: int) -> Any:
    """Return similar titles using a 3-tier discovery strategy:
      Tier 1 — same language + same genres  (e.g. Telugu Action/Drama/Thriller)
      Tier 2 — same genres, any language    (e.g. Action/Drama/Thriller worldwide)
      Tier 3 — TMDB similar/recommendations filtered to genre overlap only
    Within each tier results are sorted by TMDB rating descending.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        tmdb = get_tmdb()

        # ── 1. Fetch metadata for the reference title ──────────────────────────
        details = tmdb.get_content_details(tmdb_id=tmdb_id, content_type=content_type, region=REGION)
        orig_lang   = details.movie.language          # e.g. "te", "hi", "en"
        orig_genres = set(details.movie.genres)       # e.g. {"Action", "Drama", "Thriller"}

        # ── 2. Build genre ID lookup (needed for discover_content) ─────────────
        genres_by_type, _, _, _ = fetch_dashboard_metadata(tmdb)
        genre_name_to_id = genres_by_type.get(content_type, {})   # {"Action": 28, ...}
        genre_lookup     = {v: k for k, v in genre_name_to_id.items()}  # {28: "Action", ...}
        orig_genre_ids   = [genre_name_to_id[g] for g in orig_genres if g in genre_name_to_id]

        # ── 3. Collect results across tiers, deduplicating as we go ───────────
        seen_ids: set[int] = {tmdb_id}           # always exclude the movie itself
        tiered: list[tuple[int, Movie]] = []     # (tier_priority, Movie)

        def _ingest(raw_items: list[dict], tier: int, require_genre_overlap: bool = False) -> None:
            for item in raw_items:
                item_id = item.get("id")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                m = build_movie_from_tmdb(item, genre_lookup, None, content_type)
                if require_genre_overlap and not (orig_genres & set(m.genres)):
                    continue   # skip items with zero genre overlap
                tiered.append((tier, m))

        # Tier 1 — discover: same language + same genres (OR across genre IDs)
        if orig_genre_ids and orig_lang:
            tier1_raw = tmdb.discover_content(
                content_type=content_type,
                genre_id=orig_genre_ids,
                provider_id=None,
                language_code=orig_lang,
                region=REGION,
                pages=3,
            )
            _ingest(tier1_raw, tier=0)

        # Tier 2 — discover: same genres, any language
        if orig_genre_ids:
            tier2_raw = tmdb.discover_content(
                content_type=content_type,
                genre_id=orig_genre_ids,
                provider_id=None,
                language_code=None,
                region=REGION,
                pages=2,
            )
            _ingest(tier2_raw, tier=1)

        # Tier 3 — TMDB similar/recommendations, kept only if genre overlaps
        similar_raw = tmdb.get_similar_content(tmdb_id, content_type, pages=2)
        _ingest(similar_raw, tier=2, require_genre_overlap=True)

        # ── 4. Sort: tier first, then rating descending within tier ────────────
        tiered.sort(key=lambda x: (x[0], -x[1].rating))

        # ── 5. Serialize top 15 ────────────────────────────────────────────────
        db = get_db()
        user_id = int(session["user_id"])
        results = []
        for _, m in tiered[:15]:
            item = serialize_movie(m)
            item["is_watched"]   = db.is_in_watched_history(user_id, m.tmdb_id, m.content_type)
            item["in_watchlist"] = db.is_in_watchlist(user_id, m.tmdb_id, m.content_type)
            results.append(item)

        return jsonify({"ok": True, "similar": results, "title": details.movie.title})
    except RuntimeError as e:
        return jsonify({"ok": True, "similar": [], "title": ""})


@app.route("/api/title/<content_type>/<int:tmdb_id>/watch", methods=["POST", "OPTIONS"])
@api_login_required
def api_toggle_watch(content_type: str, tmdb_id: int) -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    db = get_db()
    user_id = int(session["user_id"])
    if db.is_in_watched_history(user_id, tmdb_id, content_type):
        db.remove_watched_item(user_id, tmdb_id, content_type)
        return jsonify({"ok": True, "is_watched": False, "message": "Removed from watched history."})
    try:
        details = get_tmdb().get_content_details(
            tmdb_id=tmdb_id, content_type=content_type, region=REGION
        )
        movie = details.movie
        db.add_watched_item(user_id, movie)
        # Remove from watchlist if present — a title can't be in both places
        db.remove_watchlist_item(user_id, tmdb_id, content_type)
        return jsonify(
            {
                "ok": True,
                "is_watched": True,
                "message": f"Added '{movie.title}' to watched history.",
            }
        )
    except RuntimeError as error:
        return tmdb_error_response(error)


@app.route("/api/title/<content_type>/<int:tmdb_id>/rating", methods=["POST", "DELETE", "OPTIONS"])
@api_login_required
def api_rating(content_type: str, tmdb_id: int) -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    db = get_db()
    user_id = int(session["user_id"])
    if request.method == "DELETE":
        db.remove_user_rating(user_id, tmdb_id, content_type)
        return jsonify({"ok": True, "message": "Removed your saved rating."})

    payload = request.get_json(silent=True) or {}
    rating_value = float(payload.get("rating", 0) or 0)
    try:
        details = get_tmdb().get_content_details(
            tmdb_id=tmdb_id, content_type=content_type, region=REGION
        )
        db.set_user_rating(user_id, details.movie, rating_value)
        return jsonify({"ok": True, "message": f"Saved your rating for '{details.movie.title}'."})
    except RuntimeError as error:
        return tmdb_error_response(error)


@app.route("/api/search/trending", methods=["GET", "OPTIONS"])
@api_login_required
def api_search_trending() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        raw = get_tmdb().get_trending(limit=16)
        trending = []
        for item in raw:
            content_type = item.get("media_type", CONTENT_TYPE_MOVIE)
            if content_type not in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
                continue
            title = item.get("title") or item.get("name") or ""
            poster = item.get("poster_path", "")
            poster_url = f"https://image.tmdb.org/t/p/w342{poster}" if poster else ""
            trending.append({
                "tmdb_id": item.get("id"),
                "title": title,
                "poster_url": poster_url,
                "content_type": content_type,
                "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
            })
        return jsonify({"ok": True, "trending": trending})
    except RuntimeError:
        return jsonify({"ok": True, "trending": []})


@app.route("/api/dashboard/ott-releases", methods=["GET", "OPTIONS"])
@api_login_required
def api_ott_releases() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        releases = get_tmdb().get_ott_releases(region=REGION, days=60)
        return jsonify({"ok": True, "releases": releases})
    except RuntimeError:
        return jsonify({"ok": True, "releases": []})


@app.route("/api/search/live", methods=["GET", "OPTIONS"])
@api_login_required
def api_search_live() -> Any:
    """Netflix-style live search.
    Fetches 2 TMDB pages (~40 candidates) then re-ranks by substring relevance so
    mid-title matches ("dark" → "The Dark Knight") surface alongside front matches.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"ok": True, "results": []})
    try:
        q_lower = query.lower()

        # Fetch 2 pages → up to ~40 unique candidates
        raw = get_tmdb().search_multi_results(query, page=1, limit=40, pages=2)

        # Re-rank by how well the query matches the title:
        #   0 = exact match
        #   1 = starts with query
        #   2 = word in title starts with query ("dark" matches "the dark knight")
        #   3 = query appears anywhere in title
        #   4 = everything else (e.g. matches via overview/cast on TMDB side)
        def relevance(item: dict) -> tuple:
            title = (item.get("title") or item.get("name") or "").lower()
            if title == q_lower:
                return (0, -float(item.get("popularity", 0)))
            if title.startswith(q_lower):
                return (1, -float(item.get("popularity", 0)))
            if any(w.startswith(q_lower) for w in title.split()):
                return (2, -float(item.get("popularity", 0)))
            if q_lower in title:
                return (3, -float(item.get("popularity", 0)))
            return (4, -float(item.get("popularity", 0)))

        raw.sort(key=relevance)

        results = [
            serialize_movie(build_movie_from_tmdb(r, {}, None, r.get("media_type", CONTENT_TYPE_MOVIE)))
            for r in raw[:20]
        ]
        return jsonify({"ok": True, "results": results})
    except RuntimeError:
        return jsonify({"ok": True, "results": []})


@app.route("/api/search/suggest", methods=["GET", "OPTIONS"])
@api_login_required
def api_search_suggest() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    query = request.args.get("query", "").strip()
    if len(query) < 2:
        return jsonify({"ok": True, "suggestions": []})
    try:
        raw = get_tmdb().search_multi_results(query, page=1, limit=8)
        suggestions = [
            serialize_movie(build_movie_from_tmdb(r, {}, None, r.get("media_type", CONTENT_TYPE_MOVIE)))
            for r in raw
        ]
        return jsonify({"ok": True, "suggestions": suggestions})
    except RuntimeError:
        return jsonify({"ok": True, "suggestions": []})


@app.route("/api/search/results", methods=["POST", "OPTIONS"])
@api_login_required
def api_search_results() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"ok": True, "results": [], "error": ""})
    db = get_db()
    user_id = int(session["user_id"])
    try:
        raw = get_tmdb().search_multi_results(query, page=1, limit=10)
        results = []
        for result in raw:
            movie = build_movie_from_tmdb(result, {}, None, result.get("media_type", CONTENT_TYPE_MOVIE))
            results.append(serialize_movie_for_user(db, user_id, movie))
        error = "" if results else "No matching result found on TMDB."
        return jsonify({"ok": True, "results": results, "error": error})
    except RuntimeError as e:
        return jsonify({"ok": True, "results": [], "error": str(e)})


# ── Daily pick server cache (date → candidate list) ──────────────────────────

_PICK_CANDIDATES_CACHE: dict[str, list[dict]] = {}
_PICK_LANG_MAP = {"en": "English", "hi": "Hindi", "te": "Telugu", "ta": "Tamil"}
_PICK_LANGUAGES = list(_PICK_LANG_MAP.keys())


def _get_daily_pick_candidates(tmdb: TMDBClient) -> list[dict]:
    """Return a stable, date-keyed pool of quality candidates for today's pick."""
    global _PICK_CANDIDATES_CACHE
    today_str = date.today().isoformat()

    # Evict stale keys (keep only today)
    _PICK_CANDIDATES_CACHE = {k: v for k, v in _PICK_CANDIDATES_CACHE.items() if k == today_str}

    if today_str in _PICK_CANDIDATES_CACHE:
        return _PICK_CANDIDATES_CACHE[today_str]

    # Fetch from TMDB in parallel across languages × content types
    tasks: list[tuple[str, str]] = [
        (lang, ct) for lang in _PICK_LANGUAGES for ct in [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]
    ]
    raw_pool: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(tmdb.get_daily_pick_candidates, lang, ct): (lang, ct)
            for lang, ct in tasks
        }
        for future in as_completed(futures):
            lang, ct = futures[future]
            try:
                for item in future.result():
                    item["_content_type"] = ct
                    item["_lang"] = lang
                    raw_pool.append(item)
            except Exception:
                pass

    # Deduplicate and rank by popularity × rating
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in sorted(
        raw_pool,
        key=lambda x: float(x.get("vote_count", 0)) * float(x.get("vote_average", 0)),
        reverse=True,
    ):
        key = (item["_content_type"], item["id"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    _PICK_CANDIDATES_CACHE[today_str] = unique
    return unique


@app.route("/api/dashboard/today-pick", methods=["GET", "OPTIONS"])
@api_login_required
def api_today_pick() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)

    tmdb = get_tmdb()
    db = get_db()
    user_id = int(session["user_id"])
    today_str = date.today().isoformat()

    candidates = _get_daily_pick_candidates(tmdb)
    if not candidates:
        return jsonify({"ok": True, "pick": None})

    # Deterministic index — same pick for all users on the same calendar day
    date_hash = int(hashlib.md5(today_str.encode()).hexdigest(), 16)
    pick = candidates[date_hash % len(candidates)]

    content_type: str = pick["_content_type"]
    tmdb_id: int = int(pick.get("id", 0))
    title: str = (pick.get("title") or pick.get("name") or "Unknown").strip()

    # Resolve genre IDs → names using cached genres or a fresh fetch
    genres_by_type = session.get("dashboard_genres_by_type") or {}
    if not genres_by_type.get(content_type):
        try:
            genres_by_type[content_type] = tmdb.get_genres(content_type)
        except RuntimeError:
            genres_by_type[content_type] = {}
    genre_id_to_name = {v: k for k, v in genres_by_type.get(content_type, {}).items()}
    genres = [genre_id_to_name[gid] for gid in pick.get("genre_ids", []) if gid in genre_id_to_name][:3]

    lang_code: str = pick.get("original_language", "")
    language: str = _PICK_LANG_MAP.get(lang_code, lang_code)

    poster_path = pick.get("poster_path") or ""
    backdrop_path = pick.get("backdrop_path") or ""
    release_date = (pick.get("release_date") or pick.get("first_air_date") or "")[:4]

    return jsonify({
        "ok": True,
        "pick": {
            "tmdb_id": tmdb_id,
            "content_type": content_type,
            "title": title,
            "overview": (pick.get("overview") or "").strip(),
            "poster_url": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "",
            "backdrop_url": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else "",
            "rating": round(float(pick.get("vote_average", 0)), 1),
            "year": release_date,
            "genres": genres,
            "language": language,
            "in_watchlist": db.is_in_watchlist(user_id, tmdb_id, content_type),
        },
    })


# ── Common streaming platform aliases ────────────────────────────────────────
_PLATFORM_ALIASES: dict[str, str] = {
    "netflix": "Netflix",
    "prime": "Amazon Prime Video",
    "amazon": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar",
    "disney": "Disney+ Hotstar",
    "sonyliv": "SonyLIV",
    "sony": "SonyLIV",
    "zee5": "Zee5",
    "zee": "Zee5",
    "aha": "Aha",
    "jiocinema": "JioCinema",
    "jio": "JioCinema",
    "apple": "Apple TV+",
    "mubi": "MUBI",
    "sun nxt": "Sun NXT",
    "sunnxt": "Sun NXT",
}

# ── Genre aliases — maps user shorthand to exact TMDB genre names ─────────────
# Values may be a single string or a list (e.g. rom-com → Romance + Comedy)
_GENRE_ALIASES: dict[str, str | list[str]] = {
    "sci-fi": "Science Fiction",
    "scifi": "Science Fiction",
    "science fiction": "Science Fiction",
    "sf": "Science Fiction",
    "rom-com": ["Romance", "Comedy"],
    "romcom": ["Romance", "Comedy"],
    "romantic comedy": ["Romance", "Comedy"],
    "rom com": ["Romance", "Comedy"],
    "superhero": "Action",
    "anime": "Animation",
    "animated": "Animation",
    "cartoon": "Animation",
    "doc": "Documentary",
    "docs": "Documentary",
    "documentary": "Documentary",
    "horror": "Horror",
    "scary": "Horror",
    "thriller": "Thriller",
    "suspense": "Thriller",
    "comedy": "Comedy",
    "funny": "Comedy",
    "drama": "Drama",
    "action": "Action",
    "adventure": "Adventure",
    "fantasy": "Fantasy",
    "mystery": "Mystery",
    "crime": "Crime",
    "western": "Western",
    "war": "War",
    "history": "History",
    "historical": "History",
    "music": "Music",
    "musical": "Music",
    "family": "Family",
    "kids": "Family",
    "animation": "Animation",
    "romance": "Romance",
    "romantic": "Romance",
}

# ── Common language aliases / alternate spellings ─────────────────────────────
_LANGUAGE_ALIASES: dict[str, str] = {
    # Direct names
    "telugu": "Telugu", "hindi": "Hindi", "tamil": "Tamil",
    "kannada": "Kannada", "malayalam": "Malayalam", "english": "English",
    "bengali": "Bengali", "marathi": "Marathi", "korean": "Korean",
    "japanese": "Japanese", "french": "French", "spanish": "Spanish",
    # Industry colloquial names — very commonly used
    "tollywood": "Telugu",
    "bollywood": "Hindi",
    "kollywood": "Tamil",
    "mollywood": "Malayalam",
    "sandalwood": "Kannada",
    "bengali cinema": "Bengali",
    "k-drama": "Korean",
    "kdrama": "Korean",
    "k drama": "Korean",
    "anime": "Japanese",   # most anime is Japanese; genre alias also handles this
}

# ── Genre siblings — used to broaden discovery when exact genre yields nothing ─
# Maps each genre to related genres that often satisfy the same intent
_GENRE_SIBLINGS: dict[str, list[str]] = {
    "Thriller":        ["Crime", "Mystery", "Action"],
    "Crime":           ["Thriller", "Mystery", "Drama"],
    "Mystery":         ["Thriller", "Crime"],
    "Horror":          ["Thriller", "Mystery"],
    "Action":          ["Thriller", "Adventure", "Crime"],
    "Adventure":       ["Action", "Fantasy", "Science Fiction"],
    "Science Fiction": ["Adventure", "Fantasy", "Thriller"],
    "Fantasy":         ["Adventure", "Science Fiction", "Animation"],
    "Romance":         ["Drama", "Comedy"],
    "Comedy":          ["Romance", "Family", "Drama"],
    "Drama":           ["Romance", "Thriller", "Crime"],
    "Animation":       ["Family", "Comedy", "Fantasy"],
    "Family":          ["Animation", "Comedy", "Drama"],
    "History":         ["Drama", "War", "Documentary"],
    "War":             ["Drama", "History", "Action"],
    "Documentary":     ["History"],
    "Music":           ["Drama", "Romance"],
    "Western":         ["Action", "Drama"],
}


def _fuzzy_match_provider(name: str, provider_options: list[str]) -> str | None:
    """Return the closest entry in provider_options for a given name (fuzzy)."""
    name_lower = name.lower().strip()
    # 1. Exact match
    for opt in provider_options:
        if opt.lower() == name_lower:
            return opt
    # 2. Either string is a substring of the other
    for opt in provider_options:
        if name_lower in opt.lower() or opt.lower() in name_lower:
            return opt
    # 3. Alias lookup → then re-check
    canonical = _PLATFORM_ALIASES.get(name_lower)
    if canonical:
        for opt in provider_options:
            if canonical.lower() in opt.lower() or opt.lower() in canonical.lower():
                return opt
    return None


def _extract_filters_keyword(
    user_message: str,
    genre_options: list[str],
    provider_options: list[str],
    language_options: list[str],
) -> dict:
    """Fast keyword-based filter extraction — runs without any API calls.
    Used as primary extraction and as fallback when Claude is unavailable.
    """
    msg = user_message.lower()

    # Genre — exact name match + alias map (handles "sci-fi" → "Science Fiction" etc.)
    genres: list[str] = []
    genre_set = set(genre_options)
    # 1. Direct match of known genre names
    for g in genre_options:
        if g.lower() in msg and g not in genres:
            genres.append(g)
    # 2. Alias lookup — iterates longest aliases first to avoid partial clobbers
    for alias in sorted(_GENRE_ALIASES, key=len, reverse=True):
        if alias in msg:
            canonical = _GENRE_ALIASES[alias]
            for name in ([canonical] if isinstance(canonical, str) else canonical):
                if name in genre_set and name not in genres:
                    genres.append(name)

    # Platform — alias + fuzzy matching
    platforms: list[str] = []
    # Direct: check every alias word
    for alias, _ in _PLATFORM_ALIASES.items():
        if alias in msg:
            matched = _fuzzy_match_provider(alias, provider_options)
            if matched and matched not in platforms:
                platforms.append(matched)
    # Also try every provider name directly
    for opt in provider_options:
        if opt.lower() in msg and opt not in platforms:
            platforms.append(opt)

    # Language — alias dict first, then option list
    languages: list[str] = []
    for alias, canonical in _LANGUAGE_ALIASES.items():
        if alias in msg and canonical in language_options and canonical not in languages:
            languages.append(canonical)
    for l in language_options:
        if l.lower() in msg and l not in languages:
            languages.append(l)

    # Content type
    content_type = ""
    if any(w in msg for w in ["movie", "film", "movies", "films"]):
        content_type = "movie"
    elif any(w in msg for w in [" show", "series", " tv ", "episode", "web series"]):
        content_type = "tv"

    return {"genres": genres, "platforms": platforms, "languages": languages, "content_type": content_type}


# Phrases that are clearly conversational with zero search intent
_GREETINGS = {"hi", "hello", "hey", "hiya", "howdy", "sup", "yo", "greetings", "good morning",
               "good afternoon", "good evening", "good night", "thanks", "thank you", "ty",
               "cool", "ok", "okay", "great", "nice", "awesome", "sounds good", "bye", "goodbye"}

_SEARCH_KEYWORDS = {
    "watch", "movie", "movies", "show", "shows", "film", "films", "series",
    "recommend", "suggest", "find", "get", "like", "similar", "best", "top",
    "good", "thriller", "drama", "comedy", "action", "horror", "sci-fi", "anime",
    "netflix", "prime", "hotstar", "platform", "genre", "language",
    "telugu", "hindi", "tamil", "english", "kannada", "malayalam",
}


def _is_general_message(message: str, genres: list, platforms: list, languages: list) -> bool:
    """Return True when the message has no movie-search intent."""
    msg = message.lower().strip().rstrip("!?.,")
    # Exact greeting match
    if msg in _GREETINGS:
        return True
    # Starts with a greeting and has no search keywords
    if any(msg.startswith(g) for g in _GREETINGS) and not any(w in msg for w in _SEARCH_KEYWORDS):
        return True
    # Short message with nothing extracted and no search vocabulary
    words = msg.split()
    if len(words) <= 4 and not genres and not platforms and not languages:
        if not any(w in msg for w in _SEARCH_KEYWORDS):
            return True
    return False


def _build_general_reply(message: str, taste_profile: dict) -> str:
    """Craft a warm, personalised conversational reply when there's no search intent."""
    msg = message.lower().strip().rstrip("!?.,")
    top_genres = taste_profile.get("top_genres", [])
    recent = taste_profile.get("recent_watched", [])

    # Farewells
    if any(w in msg for w in ("bye", "goodbye", "see you", "later")):
        return "See you! Come back whenever you need a good watch recommendation. 🎬"

    # Thanks
    if any(w in msg for w in ("thanks", "thank you", "ty", "great", "awesome", "cool", "nice", "sounds good")):
        return "Glad I could help! Let me know whenever you want more recommendations."

    # Greetings — personalise based on watch history
    if recent and top_genres:
        return (
            f"Hey! Looks like you're into {' and '.join(top_genres[:2])} lately "
            f"— {recent[0]} is a great pick. What are you in the mood for today?"
        )
    if top_genres:
        return (
            f"Hey! You seem to enjoy {' and '.join(top_genres[:2])} content. "
            f"Want me to find something along those lines, or something different?"
        )
    return (
        "Hey there! Tell me what you're in the mood for — a genre, a platform, "
        "or a movie you loved and I'll find the perfect next watch."
    )


def _extract_reference_title(message: str) -> str | None:
    """Extract a movie/show title from patterns like 'movies like Pokiri' or 'similar to Interstellar'."""
    import re
    patterns = [
        r"(?:movies?|shows?|films?|series|something|more)\s+like\s+['\"]?([^,?.!\n\"']+?)['\"]?(?:\s+(?:but|on|in|with|please|and|for)\b|\s*$)",
        r"similar\s+to\s+['\"]?([^,?.!\n\"']+?)['\"]?(?:\s+(?:but|on|in|with|please|and|for)\b|\s*$)",
        r"(?:^|get me |find me |recommend (?:me )?|suggest )(?:movies?|shows?|films?)?\s*like\s+['\"]?([^,?.!\n\"']+?)['\"]?(?:\s+(?:but|on|in|with|please|and|for)\b|\s*$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message.strip(), re.IGNORECASE)
        if match:
            title = match.group(1).strip().strip("\"'")
            # Strip trailing filler
            title = re.sub(r"\s+(?:but|please|for me|for us|thanks?)\s*$", "", title, flags=re.IGNORECASE).strip()
            if title and len(title) > 1:
                return title
    return None


def _build_taste_profile(watched: list[Movie], watchlist: list[Movie]) -> dict:
    """Derive genre/language preferences from a user's watch history and watchlist."""
    from collections import Counter
    genre_counter: Counter = Counter()
    for m in watched:
        for g in m.genres:
            genre_counter[g] += 2          # watched titles carry double weight
    for m in watchlist:
        for g in m.genres:
            genre_counter[g] += 1          # queued titles signal interest too

    lang_counter: Counter = Counter(m.language for m in watched if m.language)

    return {
        "top_genres": [g for g, _ in genre_counter.most_common(6)],
        "top_languages": [l for l, _ in lang_counter.most_common(3)],
        "recent_watched": [m.title for m in watched[:8]],
        "watchlist_titles": [m.title for m in watchlist[:5]],
    }


def _is_obvious_greeting(message: str) -> bool:
    """Fast check — no genre/provider lists needed.
    Returns True when the message is clearly small-talk with zero search intent,
    so we can skip the slow TMDB metadata fetch entirely.
    """
    msg = message.lower().strip().rstrip("!?.,")
    words = msg.split()
    if msg in _GREETINGS:
        return True
    if len(words) <= 4 and not any(w in msg for w in _SEARCH_KEYWORDS):
        if any(msg.startswith(g) for g in _GREETINGS):
            return True
    return False


def _call_claude_agent(
    user_message: str,
    history: list[dict],
    genre_options: list[str],
    provider_options: list[str],
    language_options: list[str],
    taste_profile: dict | None = None,
) -> dict:
    """Claude acts as the full agent brain.

    Returns a dict with:
      intent          : "similarity" | "filter" | "general"
      reference_title : str | None  (for intent=similarity)
      genres          : list[str]
      platforms       : list[str]
      languages       : list[str]
      content_type    : "movie" | "tv" | ""
      reply           : str   (conversational response to show the user)
    """
    import json
    import urllib.request

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}

    # ── Taste profile context ─────────────────────────────────────────────────
    profile_lines: list[str] = []
    if taste_profile:
        if taste_profile.get("top_genres"):
            profile_lines.append(f"- Favourite genres: {', '.join(taste_profile['top_genres'])}")
        if taste_profile.get("top_languages"):
            profile_lines.append(f"- Preferred languages: {', '.join(taste_profile['top_languages'])}")
        if taste_profile.get("recent_watched"):
            profile_lines.append(f"- Recently watched: {', '.join(taste_profile['recent_watched'])}")
        if taste_profile.get("watchlist_titles"):
            profile_lines.append(f"- Watchlist (want to see): {', '.join(taste_profile['watchlist_titles'])}")
    profile_block = ("\n\nUser's taste profile:\n" + "\n".join(profile_lines)) if profile_lines else ""

    system_prompt = f"""You are NextPick's intelligent movie & TV recommendation agent — a knowledgeable cinephile friend who knows world cinema deeply.{profile_block}

Available genres (use EXACT strings only): {json.dumps(genre_options)}
Available platforms (use EXACT strings only): {json.dumps(provider_options)}
Available languages (use EXACT strings only): {json.dumps(language_options)}

CRITICAL: Respond with ONLY a JSON object. No markdown fences, no text before or after.

{{
  "intent": "similarity" | "filter" | "general",
  "reference_title": "<exact title>" | null,
  "genres": [...],
  "platforms": [...],
  "languages": [...],
  "content_type": "movie" | "tv" | "",
  "reply": "<warm 1-3 sentence response>"
}}

━━ INTENT RULES ━━
• "similarity" — user wants movies/shows LIKE a specific title ("like Pokiri", "similar to Interstellar", "more like RRR"). Set reference_title. Infer genres + language from the film.
• "filter" — user wants titles by criteria (genre, platform, language, mood). Never set reference_title.
• "general" — pure conversation, no search needed. Leave all filter fields empty.

━━ EXTRACTION RULES ━━
Genres — match shorthands:
  sci-fi/scifi → "Science Fiction" | rom-com → ["Romance","Comedy"] | anime → "Animation"
  documentary → "Documentary" | superhero → "Action" | horror → "Horror"

Languages — match regional film industry names:
  Tollywood/Telugu cinema → "Telugu" | Bollywood/Hindi cinema → "Hindi"
  Kollywood/Tamil cinema → "Tamil" | Mollywood/Malayalam cinema → "Malayalam"
  Sandalwood/Kannada cinema → "Kannada" | K-drama/Korean drama → "Korean"

Platforms — fuzzy match:
  netflix/nflx → "Netflix" | prime/amazon → Amazon Prime entry | hotstar/disney+ → Disney+ Hotstar
  sony/sonyliv → "SonyLIV" | zee5/zee → "Zee5" | aha → "Aha"

━━ FILM KNOWLEDGE (for similarity intent) ━━
Pokiri/Pokkiri → Telugu, Action, Thriller | Baahubali → Telugu, Action, Adventure
KGF/KGF Chapter → Kannada, Action, Drama | RRR → Telugu, Action, Drama
Pushpa → Telugu, Action, Drama | Vikram → Tamil, Action, Thriller, Crime
Arjun Reddy/Kabir Singh → Telugu/Hindi, Drama, Romance | Ala Vaikunthapurramuloo → Telugu, Action, Comedy
Master → Tamil, Action, Thriller | Beast/Thalapathy films → Tamil, Action, Thriller
Interstellar → English, Science Fiction, Drama | Inception → English, Science Fiction, Thriller
The Dark Knight → English, Action, Thriller, Crime | Parasite → Korean, Thriller, Drama
3 Idiots/PK → Hindi, Comedy, Drama | Dangal → Hindi, Drama | Dil Chahta Hai → Hindi, Drama, Comedy
Sacred Games → Hindi, Crime, Thriller (TV) | Mirzapur → Hindi, Crime, Thriller (TV)
Use this knowledge and apply the same pattern to ANY other title you recognise.

━━ GENERIC QUERY PERSONALISATION ━━
When user asks vaguely ("what to watch", "suggest something good") and the taste profile is available,
fill genres/languages from their favourites — DO NOT leave them empty.

━━ REPLY STYLE ━━
Warm, specific, references the film title or the user's taste. Never say "I'll search for..." or "I'll find...".
"""

    messages = [{"role": msg["role"], "content": msg["content"]} for msg in history[-8:]]
    messages.append({"role": "user", "content": user_message})

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "system": system_prompt,
        "messages": messages,
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read())
        text = raw["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception:
        return {}


@app.route("/api/chat", methods=["POST", "OPTIONS"])
@api_login_required
def api_chat() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        payload = request.get_json(silent=True) or {}
        user_message = (payload.get("message") or "").strip()
        if not user_message:
            return jsonify({"ok": False, "error": "Empty message."}), 400

        db = get_db()
        user_id = int(session["user_id"])

        # ── DB calls first (fast — no network) ────────────────────────────────
        watched_from_db = db.list_watched_items(user_id)
        watchlist_from_db = db.list_watchlist_items(user_id)
        taste_profile = _build_taste_profile(watched_from_db, watchlist_from_db)
        history = db.get_chat_history(user_id, limit=12)
        db.save_chat_message(user_id, "user", user_message)

        # ── Step 1b: fast short-circuit for obvious greetings ─────────────────
        # Skip ALL network calls (TMDB + Claude) for pure small-talk.
        if _is_obvious_greeting(user_message):
            general_reply = _build_general_reply(user_message, taste_profile)
            db.save_chat_message(user_id, "assistant", general_reply)
            return jsonify({
                "ok": True,
                "reply": general_reply,
                "recommendations": [],
                "error": None,
                "filters_used": {"genres": [], "platforms": [], "languages": [], "content_type": ""},
            })

        # ── Step 1: Load TMDB metadata (may be slow on first request) ─────────
        tmdb = get_tmdb()
        genres_by_type, providers_by_type, languages, _ = fetch_dashboard_metadata(tmdb)
        genre_options = sorted({name for genres in genres_by_type.values() for name in genres.keys()})
        provider_options = sorted({p.provider_name for providers in providers_by_type.values() for p in providers})
        language_options = sorted(languages.keys())

        # Language name → code lookup for languages the user prefers
        lang_name_to_code = {v_name: code for v_name, code in languages.items()}

        # ── Step 2: keyword extraction (fast, no API) ────────────────────────────
        kw = _extract_filters_keyword(user_message, genre_options, provider_options, language_options)

        # Secondary general-message check now that we have genre/provider/language context
        if _is_general_message(user_message, kw["genres"], kw["platforms"], kw["languages"]):
            general_reply = _build_general_reply(user_message, taste_profile)
            db.save_chat_message(user_id, "assistant", general_reply)
            return jsonify({
                "ok": True,
                "reply": general_reply,
                "recommendations": [],
                "error": None,
                "filters_used": {"genres": [], "platforms": [], "languages": [], "content_type": ""},
            })

        # ── Step 3: Claude agent call (primary intelligence for real queries) ─────
        claude_out = _call_claude_agent(
            user_message, history,
            genre_options, provider_options, language_options,
            taste_profile=taste_profile,
        )
        claude_intent = claude_out.get("intent", "filter")  # similarity | filter | general

        # ── Step 4: merge — Claude wins where it has values, keyword fills gaps ───
        def _merge_list(claude_val, kw_val, valid_set):
            merged, seen = [], set()
            for item in (claude_val or []) + (kw_val or []):
                if item and item not in seen and item in valid_set:
                    merged.append(item); seen.add(item)
            return merged

        genre_set = set(genre_options)
        lang_set  = set(language_options)

        genres_filter   = _merge_list(claude_out.get("genres"),    kw.get("genres"),    genre_set)
        language_names  = _merge_list(claude_out.get("languages"), kw.get("languages"), lang_set)

        raw_platforms = list(dict.fromkeys((claude_out.get("platforms") or []) + (kw.get("platforms") or [])))
        platforms_filter: list[str] = []
        for p in raw_platforms:
            matched = _fuzzy_match_provider(p, provider_options)
            if matched and matched not in platforms_filter:
                platforms_filter.append(matched)

        content_type_raw = (claude_out.get("content_type") or kw.get("content_type") or "").strip()
        content_types = [content_type_raw] if content_type_raw in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV) else None

        # ── Reference title: Claude's detection takes priority, regex as backup ───
        reference_title = (
            (claude_out.get("reference_title") or "").strip()
            or _extract_reference_title(user_message)
        )

        bot_reply = (claude_out.get("reply") or "").strip()
        if not bot_reply:
            if reference_title:
                bot_reply = f"Finding movies with the same feel as {reference_title}!"
            else:
                filter_parts = []
                if language_names: filter_parts.append(language_names[0])
                if genres_filter:  filter_parts.append("/".join(genres_filter))
                if platforms_filter: filter_parts.append(f"on {platforms_filter[0]}")
                bot_reply = (
                    f"Searching for {' '.join(filter_parts)} — here's what I found!"
                    if filter_parts else "Here are some picks tailored to your taste!"
                )

        language_codes = [languages[l] for l in language_names if l in languages]
        rec_language = language_codes[0] if len(language_codes) == 1 else None

        # Combined preference list for the recommender
        preference_history = watched_from_db + watchlist_from_db

        # ── "general" intent: Claude answered conversationally, no search needed ──
        if claude_intent == "general" and not reference_title and not genres_filter and not platforms_filter:
            db.save_chat_message(user_id, "assistant", bot_reply)
            return jsonify({"ok": True, "reply": bot_reply, "recommendations": [], "error": None,
                            "filters_used": {"genres": [], "platforms": [], "languages": [], "content_type": ""}})

        recommendations = []
        error_msg = None

        if reference_title:
            # ── Path A: Similarity-based recommendations ─────────────────────────
            try:
                ref_results = tmdb.search_multi_results(reference_title, page=1, limit=5)
                ref_item = next(
                    (r for r in ref_results if r.get("media_type") in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)),
                    None,
                )
                if ref_item:
                    ref_ct = ref_item.get("media_type", CONTENT_TYPE_MOVIE)
                    ref_tmdb_id = int(ref_item.get("id", 0))
                    ref_genre_lookup = {gid: gname for gname, gid in genres_by_type.get(ref_ct, {}).items()}
                    ref_movie = build_movie_from_tmdb(ref_item, ref_genre_lookup, None, ref_ct)

                    if not bot_reply or "Finding movies" in bot_reply:
                        vibe = ", ".join(ref_movie.genres[:2]) if ref_movie.genres else "similar"
                        bot_reply = (
                            f"Great pick! {ref_movie.title} is known for its {vibe} feel — "
                            f"here are movies with the same vibe!"
                        )

                    similar_raw = tmdb.get_similar_content(ref_tmdb_id, ref_ct, pages=3)

                    seen: set = set()
                    catalog: list[Movie] = []
                    for item in similar_raw:
                        movie = build_movie_from_tmdb(item, ref_genre_lookup, None, ref_ct)
                        key = (movie.content_type, movie.tmdb_id)
                        if key not in seen:
                            seen.add(key)
                            catalog.append(movie)

                    if catalog:
                        similarity_history = [ref_movie] + preference_history
                        recommender = MovieRecommender(catalog)
                        recs = recommender.recommend(
                            genres=ref_movie.genres or None,
                            platform=platforms_filter[0] if platforms_filter else None,
                            language=None,
                            watched_history=similarity_history,
                            top_k=None,
                        )
                        recs = filter_recommendations_against_watched(recs, watched_from_db)
                        recommendations = [serialize_recommendation(r) for r in recs[:12]]

                    if not recommendations:
                        fallback_genres = ref_movie.genres[:2] if ref_movie.genres else None
                        fallback_lang = ref_movie.language or None
                        if fallback_genres or fallback_lang:
                            fb_catalog = []
                            fb_seen: set = set()
                            for movie in discover_catalog(
                                tmdb=tmdb,
                                genre_names=fallback_genres,
                                provider_name=platforms_filter[0] if platforms_filter else None,
                                language_code=fallback_lang,
                                genres_by_type=genres_by_type,
                                providers_by_type=providers_by_type,
                                content_types=[ref_ct],
                            ):
                                key = (movie.content_type, movie.tmdb_id)
                                if key not in fb_seen:
                                    fb_seen.add(key)
                                    fb_catalog.append(movie)
                            if fb_catalog:
                                fb_recs = MovieRecommender(fb_catalog).recommend(
                                    genres=fallback_genres,
                                    platform=None,
                                    language=None,
                                    watched_history=[ref_movie] + preference_history,
                                    top_k=None,
                                )
                                fb_recs = filter_recommendations_against_watched(fb_recs, watched_from_db)
                                recommendations = [serialize_recommendation(r) for r in fb_recs[:12]]

                    if not recommendations:
                        error_msg = f"Couldn't find similar titles for '{ref_movie.title}'. Try a different movie name."
                else:
                    error_msg = f"Couldn't find '{reference_title}' on TMDB. Check the spelling and try again."
            except RuntimeError as e:
                error_msg = f"Lookup failed: {e}"

        else:
            # ── Path B: Filter-based discovery with progressive fallback ──────────
            discovery_genres = genres_filter or None
            if not discovery_genres and taste_profile["top_genres"]:
                discovery_genres = taste_profile["top_genres"][:3]

            discovery_lang_codes = language_codes
            if not discovery_lang_codes and taste_profile["top_languages"]:
                top_lang_code = languages.get(taste_profile["top_languages"][0])
                if top_lang_code:
                    discovery_lang_codes = [top_lang_code]

            sibling_genres: list[str] = list(genres_filter) if genres_filter else []
            for g in genres_filter:
                for sg in _GENRE_SIBLINGS.get(g, []):
                    if sg not in sibling_genres and sg in genre_set:
                        sibling_genres.append(sg)

            def _build_catalog(genre_names, plat_list, lang_codes, ctypes):
                seen_: set = set()
                cat_: list[Movie] = []
                codes_ = lang_codes if lang_codes else [None]
                for lang in codes_:
                    for plat in (plat_list if plat_list else [None]):
                        for mv in discover_catalog(
                            tmdb=tmdb, genre_names=genre_names,
                            provider_name=plat, language_code=lang,
                            genres_by_type=genres_by_type,
                            providers_by_type=providers_by_type,
                            content_types=ctypes,
                        ):
                            k = (mv.content_type, mv.tmdb_id)
                            if k not in seen_:
                                seen_.add(k); cat_.append(mv)
                return cat_

            def _score_and_filter(catalog, post_genre_set, post_lang):
                if not catalog:
                    return []
                recs_ = MovieRecommender(catalog).recommend(
                    genres=genres_filter or None, platform=None,
                    language=rec_language, watched_history=preference_history, top_k=None,
                )
                recs_ = filter_recommendations_against_watched(recs_, watched_from_db)
                if post_genre_set:
                    recs_ = [r for r in recs_ if post_genre_set & set(r.movie.genres)]
                if post_lang:
                    recs_ = [r for r in recs_ if r.movie.language == post_lang]
                return recs_

            try:
                relaxed_note = ""

                cat1 = _build_catalog(discovery_genres, platforms_filter, discovery_lang_codes, content_types)
                recs = _score_and_filter(cat1, set(genres_filter) if genres_filter else set(), rec_language if genres_filter or rec_language else None)

                if len(recs) < 3 and sibling_genres != list(genres_filter or []):
                    cat2 = _build_catalog(sibling_genres or discovery_genres, platforms_filter, discovery_lang_codes, content_types)
                    recs2 = _score_and_filter(cat2, set(sibling_genres) if genres_filter else set(), rec_language if rec_language else None)
                    if len(recs2) > len(recs):
                        recs = recs2
                        if genres_filter and sibling_genres != list(genres_filter):
                            relaxed_note = " (including similar genres)"

                if len(recs) < 3 and platforms_filter:
                    cat3 = _build_catalog(sibling_genres or discovery_genres, [], discovery_lang_codes, content_types)
                    recs3 = _score_and_filter(cat3, set(sibling_genres) if genres_filter else set(), rec_language if rec_language else None)
                    if len(recs3) > len(recs):
                        recs = recs3
                        relaxed_note = f" (not limited to {platforms_filter[0]})"

                if len(recs) < 3 and genres_filter:
                    cat4 = _build_catalog(None, [], discovery_lang_codes, content_types)
                    recs4 = _score_and_filter(cat4, set(), rec_language if rec_language else None)
                    if len(recs4) > len(recs):
                        recs = recs4
                        relaxed_note = " (broader results — exact genre unavailable)"

                recommendations = [serialize_recommendation(r) for r in recs[:12]]

                if relaxed_note and bot_reply:
                    bot_reply = bot_reply.rstrip("!.") + relaxed_note + "."

                if not recommendations:
                    parts = [p for p in [language_names[0] if language_names else "", ", ".join(genres_filter)] if p]
                    error_msg = (
                        f"Couldn't find {''.join(parts+[' '])}titles right now. "
                        "TMDB may not have enough data for this combination — try different filters."
                    )

            except RuntimeError as e:
                error_msg = f"Discovery failed: {e}"

        # Persist bot reply and return
        db.save_chat_message(user_id, "assistant", bot_reply)

        return jsonify({
            "ok": True,
            "reply": bot_reply,
            "recommendations": recommendations,
            "error": error_msg,
            "filters_used": {
                "genres": genres_filter,
                "platforms": platforms_filter,
                "languages": language_names,
                "content_type": content_type_raw,
            },
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": f"Something went wrong on the server: {exc}",
            "reply": "Sorry, I ran into an error. Please try again.",
            "recommendations": [],
            "filters_used": {},
        }), 500


@app.route("/api/chat/history", methods=["GET", "OPTIONS"])
@api_login_required
def api_chat_history() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    user_id = int(session["user_id"])
    history = get_db().get_chat_history(user_id, limit=40)
    return jsonify({"ok": True, "history": history})


@app.route("/api/chat/clear", methods=["POST", "OPTIONS"])
@api_login_required
def api_chat_clear() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    get_db().clear_chat_history(int(session["user_id"]))
    return jsonify({"ok": True})


@app.route("/", methods=["GET"])
def index() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "movie-bot-api",
            "frontend": "Next.js",
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
