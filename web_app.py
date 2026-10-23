from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time

# Load .env file if present (development convenience)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from typing import Any
import re

from flask import Flask, has_request_context, jsonify, request, session

from src.db import Database
from src.recommender import Movie, MovieRecommender, Recommendation
from src.tmdb_client import CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV, TMDBClient, build_movie_from_tmdb

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "app.db"
REGION = "IN"   # default region for new users and for background work with no user
PAGES_TO_SCAN = 5

# Countries a user can pick as their region. A curated set of major streaming markets
# rather than all ~140 TMDB supports. Order here is the order shown in the UI.
SUPPORTED_REGIONS: dict[str, str] = {
    "IN": "India",
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "NZ": "New Zealand",
    "IE": "Ireland",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "SE": "Sweden",
    "BR": "Brazil",
    "MX": "Mexico",
    "AR": "Argentina",
    "JP": "Japan",
    "KR": "South Korea",
    "SG": "Singapore",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "PH": "Philippines",
    "TH": "Thailand",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "ZA": "South Africa",
    "TR": "Turkey",
}

# TMDB lists every "provider" in a region: rent/buy stores, add-on channels sold through
# another app, kids/ad-tier duplicates and tiny niche services alongside the real
# streaming platforms. These filters keep the platform picker to services people
# actually subscribe to.
_PROVIDER_NOISE = re.compile(
    r"amazon channel|apple tv channel|roku premium channel|\bstore\b|\bvod\b|google play"
    r"|amazon video|justwatch|rakuten tv|fandango|with ads|\bkids\b|^youtube$|\bessential\b",
    re.IGNORECASE,
)
# Add-on "channels" and ad-tier listings that just repeat a platform already shown on a
# title's "Available on" row (rent/buy stores stay — they are real ways to watch).
_PROVIDER_DUPLICATE = re.compile(r"amazon channel|apple tv channel|roku premium channel|with ads|justwatch", re.IGNORECASE)
_NICHE_PROVIDERS = {
    "GuideDoc", "Curiosity Stream", "DOCSVILLE", "Magellan TV", "WOW Presents Plus",
    "BroadwayHD", "Filmzie", "Dekkoo", "True Story", "DocAlliance Films", "Eventive",
    "Cultpix", "Takflix", "FilmBox+", "Plex", "Plex Channel", "Hoopla", "Kanopy",
    "Spamflix", "Tubi TV", "Shudder", "Kocowa", "Viki", "AsianCrush", "Midnight Pulp",
    "Argo", "Pluto TV", "Jolt Film", "FOUND TV", "Bloodstream", "Public Domain Movies",
    "Brew", "OnDemandKorea", "Univer Video", "MovieMe", "KableOne",
}
# Regional services that are mainstream at home but show up as filler elsewhere.
_HOME_ONLY_PROVIDERS = {"Sun Nxt": {"IN"}, "Hoichoi": {"IN"}, "aha": {"IN"}}
# Major local platforms that TMDB ranks too low to make the cut on their own.
_PINNED_PROVIDERS = {"IN": ["aha"]}
TOP_PROVIDER_COUNT = 11
# India's long-standing "recent arrivals" set: Netflix, Prime Video, Hotstar (old + new
# ids), Zee5, Sony Liv, aha, Sun Nxt. Other regions use their own top platforms.
_INDIA_OTT_PROVIDER_IDS = [8, 119, 122, 2336, 232, 237, 532, 309]
_INDIA_OTT_PROVIDER_NAMES = ["Netflix", "Prime Video", "JioHotstar"]

app = Flask(__name__)
# Render sets RENDER=true. There, a missing secret or database is a deploy error rather
# than something to fall back from: a guessable secret lets anyone forge a login, and
# without DATABASE_URL every account would live on a disk that is wiped on each restart.
IN_PRODUCTION = os.getenv("RENDER", "").strip().lower() == "true"
_secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
if IN_PRODUCTION and (len(_secret_key) < 16 or _secret_key == "dev-secret-change-me"):
    raise RuntimeError("FLASK_SECRET_KEY must be set to a long random value in production.")
if IN_PRODUCTION and not os.getenv("DATABASE_URL", "").strip():
    raise RuntimeError(
        "DATABASE_URL must point to the production Postgres database (e.g. Neon); "
        "without it all user data is lost whenever the server restarts."
    )
app.secret_key = _secret_key or "dev-secret-change-me"
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),   # stay signed in, not just until the browser closes
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IN_PRODUCTION,             # HTTPS-only cookie on the live site
)
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


def current_region() -> str:
    """The signed-in user's region — decides which platforms, availability and
    certifications they see. Cached in the session; falls back to the default outside
    a request (background warm-up) or for an unknown value."""
    if not has_request_context():
        return REGION
    region = session.get("region")
    if region in SUPPORTED_REGIONS:
        return region
    user_id = session.get("user_id")
    region = (get_db().get_user_region(int(user_id)) if user_id else None) or REGION
    if region not in SUPPORTED_REGIONS:
        region = REGION
    if user_id:
        session["region"] = region
    return region


def top_providers(providers_by_type: dict[str, list[Any]], region: str, limit: int = TOP_PROVIDER_COUNT) -> list[Any]:
    """The region's most prominent real streaming platforms, most popular first."""
    best: dict[str, Any] = {}
    for providers in providers_by_type.values():
        for provider in providers:
            name = provider.provider_name
            if _PROVIDER_NOISE.search(name) or name in _NICHE_PROVIDERS:
                continue
            if name in _HOME_ONLY_PROVIDERS and region not in _HOME_ONLY_PROVIDERS[name]:
                continue
            if name not in best or provider.priority < best[name].priority:
                best[name] = provider
    ranked = sorted(best.values(), key=lambda provider: (provider.priority, provider.provider_name))
    pinned = [best[name] for name in _PINNED_PROVIDERS.get(region, []) if name in best]
    head = [provider for provider in ranked if provider not in pinned][: max(0, limit - len(pinned))]
    return head + pinned


def ott_providers_for(tmdb: TMDBClient, region: str) -> tuple[list[int], list[str]]:
    """(provider ids, display names) of the subscription platforms whose recent arrivals
    are shown on the home page for this region."""
    if region == "IN":
        return _INDIA_OTT_PROVIDER_IDS, _INDIA_OTT_PROVIDER_NAMES
    providers = top_providers(
        {ctype: tmdb.get_providers(ctype, region, 3) for ctype in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)},
        region,
        limit=8,
    )
    return [p.provider_id for p in providers], [p.provider_name for p in providers]


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


def fetch_dashboard_metadata(tmdb: TMDBClient, region: str | None = None) -> tuple[dict[str, dict[str, int]], dict[str, list[Any]], dict[str, str], str | None]:
    """Genres, providers (for the region) and languages. TMDBClient caches these for 24h,
    so repeat calls are memory hits."""
    region = region or current_region()   # resolved here: the worker threads below have no session
    genres_by_type: dict[str, dict[str, int]] = {CONTENT_TYPE_MOVIE: {}, CONTENT_TYPE_TV: {}}
    providers_by_type: dict[str, list[Any]] = {CONTENT_TYPE_MOVIE: [], CONTENT_TYPE_TV: []}
    languages: dict[str, str] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {}
        for ctype in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
            future_map[executor.submit(tmdb.get_genres, ctype)] = ("genres", ctype)
            future_map[executor.submit(tmdb.get_providers, ctype, region, 3)] = ("providers", ctype)
        future_map[executor.submit(tmdb.get_languages)] = ("languages", None)

        for future in as_completed(future_map):
            task_type, content_type = future_map[future]
            try:
                result = future.result()
                if task_type == "genres" and content_type:
                    genres_by_type[content_type] = result
                elif task_type == "providers" and content_type:
                    providers_by_type[content_type] = result
                else:
                    languages = result
            except Exception as error:
                errors.append(str(error))

    metadata_error = "; ".join(dict.fromkeys(errors)) if errors else None
    return genres_by_type, providers_by_type, languages, metadata_error


# TMDB's movie and TV genre lists don't line up — e.g. "Adventure" and "Thriller"
# are movie-only genres, with no TV equivalent of "Thriller" at all. The dashboard's
# genre picker shows the union of both lists, so a user filtering to TV Shows can
# still select a movie-only genre. These aliases translate the request to the closest
# real TV genre where one exists.
TV_GENRE_ALIASES: dict[str, str] = {
    "Action": "Action & Adventure",
    "Adventure": "Action & Adventure",
    "Fantasy": "Sci-Fi & Fantasy",
    "Science Fiction": "Sci-Fi & Fantasy",
    "War": "War & Politics",
}


def discover_catalog(
    tmdb: TMDBClient,
    genre_names: list[str] | None,
    provider_name: str | None,
    language_code: str | None,
    genres_by_type: dict[str, dict[str, int]],
    providers_by_type: dict[str, list[Any]],
    content_types: list[str] | None = None,
    region: str | None = None,
    year_range: tuple[int | None, int | None] | None = None,
    runtime_max: int | None = None,
) -> list[Movie]:
    region = region or current_region()
    year_from, year_to = year_range or (None, None)
    catalog: list[Movie] = []
    provider_name_to_id_by_type = {
        ctype: {provider.provider_name: provider.provider_id for provider in providers}
        for ctype, providers in providers_by_type.items()
    }

    for content_type in (content_types or [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]):
        # Resolve genre names → IDs for this content type; pass as a list for OR-filtering.
        genre_ids: list[int] | None = None
        if genre_names:
            type_genres = genres_by_type[content_type]
            resolved: list[int] = []
            for g in genre_names:
                if g in type_genres:
                    resolved.append(type_genres[g])
                elif content_type == CONTENT_TYPE_TV and TV_GENRE_ALIASES.get(g) in type_genres:
                    resolved.append(type_genres[TV_GENRE_ALIASES[g]])
            resolved = list(dict.fromkeys(resolved))  # dedupe, keep order
            # None of the requested genres exist for this content type (e.g. "Thriller"
            # for TV, which has no equivalent at all). Rather than returning zero results,
            # fetch this content type unfiltered by genre — the recommender still ranks by
            # quality/history/language, so the primary tier stays populated instead of
            # falling straight through to the "relaxed filters" fallback.
            if resolved:
                genre_ids = resolved

        provider_id = provider_name_to_id_by_type[content_type].get(provider_name) if provider_name else None
        raw_items = tmdb.discover_content(
            content_type=content_type,
            genre_id=genre_ids,
            provider_id=provider_id,
            language_code=language_code,
            region=region,
            pages=PAGES_TO_SCAN,
            year_from=year_from,
            year_to=year_to,
            runtime_max=runtime_max,
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


def build_watched_pick_section(
    tmdb: TMDBClient,
    genres_by_type: dict[str, dict[str, int]],
    watched_from_db: list[Movie],
    excluded_keys: set[tuple[str, int]],
    region: str | None = None,
) -> dict[str, Any] | None:
    """'Since you've watched X, we picked these for you' — seeded from the most recently
    watched title (list_watched_items() already orders newest-first), matched against
    candidates that share EITHER its genres OR one of its top-billed cast members.
    Returns None for first-time users with no history, or when too few candidates turn up.
    """
    if not watched_from_db:
        return None

    region = region or current_region()
    seed = watched_from_db[0]
    try:
        seed_details = tmdb.get_content_details(seed.tmdb_id, seed.content_type, region=region)
    except RuntimeError:
        return None

    type_genres = genres_by_type.get(seed.content_type, {})
    seed_genre_ids = [type_genres[g] for g in seed_details.genres if g in type_genres]
    top_cast_ids = [c["id"] for c in seed_details.cast_full[:5] if c.get("id")]
    if not seed_genre_ids and not top_cast_ids:
        return None

    genre_lookup = {gid: gname for gname, gid in type_genres.items()}
    seen_local: set[tuple[str, int]] = set(excluded_keys)
    seen_local.add((seed.content_type, seed.tmdb_id))
    candidates: list[Movie] = []

    def _ingest(raw_items: list[dict]) -> None:
        for item in raw_items:
            item_id = item.get("id")
            key = (seed.content_type, item_id)
            if not item_id or key in seen_local:
                continue
            seen_local.add(key)
            candidates.append(build_movie_from_tmdb(item, genre_lookup, None, seed.content_type))

    try:
        if seed_genre_ids:
            _ingest(tmdb.discover_content(
                content_type=seed.content_type, genre_id=seed_genre_ids, provider_id=None,
                language_code=None, region=region, pages=2,
            ))
        if top_cast_ids:
            _ingest(tmdb.discover_content(
                content_type=seed.content_type, genre_id=None, provider_id=None,
                language_code=None, region=region, pages=2, cast_id=top_cast_ids,
            ))
    except RuntimeError:
        pass

    if len(candidates) < 4:
        return None

    # recommend()'s own watched-history exclusion drops the seed (and everything else
    # already watched) automatically, since watched_from_db is passed as watched_history.
    recs = MovieRecommender(candidates).recommend(
        genres=seed_details.genres or None, platform=None, language=None,
        watched_history=watched_from_db, top_k=None,
    )
    recs = [r for r in recs if (r.movie.content_type, r.movie.tmdb_id) not in excluded_keys][:8]
    if not recs:
        return None

    return {
        "seed_title": seed.title,
        "seed_tmdb_id": seed.tmdb_id,
        "seed_content_type": seed.content_type,
        "seed_poster_url": seed.poster_url,
        "items": recs,
    }


REWATCH_COUNT = 10
# The rewatch picks are worked out alongside the main search, so their lookups don't add
# to a request that can already take a while.
_rewatch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rewatch")
# Where a title streams isn't stored with the watch history, so it is looked up per
# title — for at most this many of the most recent watches, in parallel and cached.
REWATCH_PLATFORM_LOOKUPS = 60


def build_rewatch_picks(
    tmdb: TMDBClient,
    watched_from_db: list[Movie],
    filters: dict[str, Any],
    language_codes: dict[str, str],
    region: str,
) -> list[Recommendation]:
    """'You can rewatch these and relive the moments' — titles from the user's own watch
    history that share at least one of the chosen filters (format, genre, platform,
    language). The more they share, the higher they rank; ties go to the titles the user
    rated highest, then to the most recently watched. Each pick's reasons say what it
    shares. With no filters chosen, simply their best-rated recent watches."""
    content_type = filters.get("content_type") or ""
    wanted_genres = _with_tv_genres(filters.get("genres") or [])
    platforms = set(filters.get("platforms") or [])
    language_by_code = {
        language_codes[name]: name for name in filters.get("languages") or [] if name in language_codes
    }
    any_filter = bool(content_type or wanted_genres or platforms or language_by_code)

    streams_on: dict[tuple[str, int], set[str]] = {}
    if platforms:
        recent = watched_from_db[:REWATCH_PLATFORM_LOOKUPS]
        details = tmdb.map_parallel(
            lambda m: tmdb.get_content_details(m.tmdb_id, m.content_type, region=region), recent
        )
        streams_on = {
            (m.content_type, m.tmdb_id): set(d.providers) for m, d in zip(recent, details) if d is not None
        }

    ranked: list[tuple[int, float, int, Movie, list[str]]] = []
    for position, movie in enumerate(watched_from_db):
        shared: list[str] = []
        if content_type and movie.content_type == content_type:
            shared.append(content_label(content_type))
        genre = next((g for g in movie.genres if g in wanted_genres), None)
        if genre:
            shared.append(genre)
        available = sorted(platforms & streams_on.get((movie.content_type, movie.tmdb_id), set()))
        if available:
            shared.append(f"On {available[0]}")
        if movie.language in language_by_code:
            shared.append(language_by_code[movie.language])
        if any_filter and not shared:
            continue
        ranked.append((len(shared), movie.user_rating or 0.0, -position, movie, shared))
    ranked.sort(key=lambda entry: entry[:3], reverse=True)
    return [
        Recommendation(movie=movie, score=float(count), reasons=shared)
        for count, _, _, movie, shared in ranked[:REWATCH_COUNT]
    ]


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
    region = current_region()
    watched_from_db = db.list_watched_items(user_id)
    genres_by_type, providers_by_type, languages, metadata_error = fetch_dashboard_metadata(tmdb, region)

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
                    region=region,
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

    # ── 3-tier smart fallbacks ────────────────────────────────────────────────
    # Only computed when primary results are fewer than FALLBACK_THRESHOLD.
    # Computing all 3 tiers eagerly (up to 15 TMDB calls) caused request
    # timeouts on the frontend when filters were strict (e.g. Telugu + 3 platforms).
    FALLBACK_SIZE = 16  # fetch 16 so frontend can show 8 + keep 8 in reserve
    FALLBACK_THRESHOLD = 16  # only run fallbacks when primary results < this
    fallback_other_platforms: list[Recommendation] = []
    fallback_other_genres: list[Recommendation] = []
    fallback_other_languages: list[Recommendation] = []
    watched_pick: dict[str, Any] | None = None

    if filters.get("active"):
        excluded_keys: set[tuple[str, int]] = (
            {(m.content_type, m.tmdb_id) for m in watched_from_db}
            | {(r.movie.content_type, r.movie.tmdb_id) for r in recommendations}
        )

        if len(recommendations) < FALLBACK_THRESHOLD:
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
                    excluded_keys |= {(r.movie.content_type, r.movie.tmdb_id) for r in fallback_other_languages}
                except RuntimeError:
                    pass

        # "Since you've watched X, we picked these for you" — only for returning users
        # with real history; shown alongside the fallbacks once "Show me more" is clicked.
        watched_pick = build_watched_pick_section(tmdb, genres_by_type, watched_from_db, excluded_keys, region)

    return {
        "genre_options": genre_options,
        "provider_options": provider_options,
        "top_providers": [p.provider_name for p in top_providers(providers_by_type, region)],
        "region": region,
        "region_name": SUPPORTED_REGIONS[region],
        "language_options": language_options,
        "watched_items": watched_from_db,
        "recommendations": recommendations[:40],
        "recommendations_error": recommendations_error,
        "fallback_other_platforms": fallback_other_platforms,
        "fallback_other_genres": fallback_other_genres,
        "fallback_other_languages": fallback_other_languages,
        "watched_pick": watched_pick,
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
    username_value = session.get("username")
    if not username_value:
        username_value = get_db().get_username(int(user_id))
        session["username"] = username_value
    return jsonify(
        {
            "ok": True,
            "authenticated": True,
            "user": {"id": int(user_id), "username": username_value, "region": current_region()},
            "regions": [{"code": code, "name": name} for code, name in SUPPORTED_REGIONS.items()],
        }
    )


@app.route("/api/settings/region", methods=["POST", "OPTIONS"])
@api_login_required
def api_set_region() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    region = str(payload.get("region") or "").strip().upper()
    if region not in SUPPORTED_REGIONS:
        return jsonify({"ok": False, "error": "Unsupported region."}), 400
    get_db().set_user_region(int(session["user_id"]), region)
    session["region"] = region
    # Saved recommendation filters name platforms from the old region.
    session.pop("dashboard_filters", None)
    return jsonify({"ok": True, "region": region, "region_name": SUPPORTED_REGIONS[region]})


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
    username_value = get_db().get_username(int(user_id))
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["username"] = username_value
    return jsonify(
        {
            "ok": True,
            "user": {"id": int(user_id), "username": username_value},
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


def dashboard_payload(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "genre_options": view["genre_options"],
        "provider_options": view["provider_options"],
        "top_providers": view["top_providers"],
        "region": view["region"],
        "region_name": view["region_name"],
        "language_options": view["language_options"],
        "watched_items": [serialize_movie(item) for item in view["watched_items"]],
        "recommendations": [serialize_recommendation(rec) for rec in view["recommendations"]],
        "recommendations_error": view["recommendations_error"],
        "fallback_other_platforms": [serialize_recommendation(rec) for rec in view["fallback_other_platforms"]],
        "fallback_other_genres": [serialize_recommendation(rec) for rec in view["fallback_other_genres"]],
        "fallback_other_languages": [serialize_recommendation(rec) for rec in view["fallback_other_languages"]],
        "watched_pick": (
            {**view["watched_pick"], "items": [serialize_recommendation(rec) for rec in view["watched_pick"]["items"]]}
            if view["watched_pick"] else None
        ),
        "metadata_error": view["metadata_error"],
        "filters": view["filters"],
    }


@app.route("/api/dashboard", methods=["GET", "OPTIONS"])
@api_login_required
def api_dashboard() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(dashboard_payload(get_dashboard_view(int(session["user_id"]))))


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
    user_id = int(session["user_id"])
    rewatch_job = None
    if payload.get("rewatch"):
        # "Don't mind rewatching": also titles from their own history that fit the filters.
        tmdb, region = get_tmdb(), current_region()
        languages = fetch_dashboard_metadata(tmdb, region)[2]
        rewatch_job = _rewatch_pool.submit(
            build_rewatch_picks, tmdb, get_db().list_watched_items(user_id),
            dict(session["dashboard_filters"]), languages, region,
        )
    response = dashboard_payload(get_dashboard_view(user_id))
    if rewatch_job is not None:
        response["rewatch_picks"] = [serialize_recommendation(rec) for rec in rewatch_job.result()]
    return jsonify(response)


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
            tmdb_id=tmdb_id, content_type=content_type, region=current_region()
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
    region = current_region()
    try:
        details = get_tmdb().get_content_details(
            tmdb_id=tmdb_id, content_type=content_type, region=region
        )
        movie = details.movie
        user_id = int(session["user_id"])
        saved_rating, is_watched, in_watchlist = db.get_title_state(
            user_id, movie.tmdb_id, movie.content_type
        )
        return jsonify(
            {
                "ok": True,
                "movie": serialize_movie(movie),
                "genres": details.genres,
                "cast": details.cast,
                "cast_full": details.cast_full,
                "providers": [name for name in details.providers if not _PROVIDER_DUPLICATE.search(name)],
                "original_rating": details.original_rating,
                "certification": details.certification,
                "trailer": details.trailer,
                "saved_rating": saved_rating,
                "is_watched": is_watched,
                "in_watchlist": in_watchlist,
                "region": region,
                "region_name": SUPPORTED_REGIONS[region],
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
        region = current_region()   # read here: the tier lookups below run on worker threads

        # ── 1. Fetch metadata for the reference title ──────────────────────────
        details = tmdb.get_content_details(tmdb_id=tmdb_id, content_type=content_type, region=region)
        orig_lang   = details.movie.language          # e.g. "te", "hi", "en"
        orig_genres = set(details.movie.genres)       # e.g. {"Action", "Drama", "Thriller"}

        # ── 2. Build genre ID lookup (needed for discover_content) ─────────────
        genres_by_type, _, _, _ = fetch_dashboard_metadata(tmdb, region)
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

        # Tiers 1-3 are independent TMDB lookups — run them concurrently.
        def _tier1():
            if not (orig_genre_ids and orig_lang):
                return []
            return tmdb.discover_content(
                content_type=content_type, genre_id=orig_genre_ids, provider_id=None,
                language_code=orig_lang, region=region, pages=3,
            )

        def _tier2():
            if not orig_genre_ids:
                return []
            return tmdb.discover_content(
                content_type=content_type, genre_id=orig_genre_ids, provider_id=None,
                language_code=None, region=region, pages=2,
            )

        def _tier3():
            return tmdb.get_similar_content(tmdb_id, content_type, pages=2)

        tier1_raw, tier2_raw, similar_raw = (
            r or [] for r in tmdb.map_parallel(lambda fn: fn(), [_tier1, _tier2, _tier3])
        )
        _ingest(tier1_raw, tier=0)
        _ingest(tier2_raw, tier=1)
        _ingest(similar_raw, tier=2, require_genre_overlap=True)

        # ── 4. Sort: tier first, then rating descending within tier ────────────
        tiered.sort(key=lambda x: (x[0], -x[1].rating))

        # ── 5. Serialize top 15 ────────────────────────────────────────────────
        db = get_db()
        user_id = int(session["user_id"])
        watched_keys, watchlist_keys = db.get_status_keys(user_id)
        results = []
        for _, m in tiered[:15]:
            item = serialize_movie(m)
            item["is_watched"]   = (m.tmdb_id, m.content_type) in watched_keys
            item["in_watchlist"] = (m.tmdb_id, m.content_type) in watchlist_keys
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
            tmdb_id=tmdb_id, content_type=content_type, region=current_region()
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
            tmdb_id=tmdb_id, content_type=content_type, region=current_region()
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
        tmdb = get_tmdb()
        region = current_region()
        provider_ids, provider_names = ott_providers_for(tmdb, region)
        if not provider_ids:
            return jsonify({"ok": True, "releases": [], "providers": []})
        releases = tmdb.get_ott_releases(region=region, days=60, provider_ids=provider_ids)
        return jsonify({"ok": True, "releases": releases, "providers": provider_names})
    except RuntimeError:
        return jsonify({"ok": True, "releases": [], "providers": []})


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
        from concurrent.futures import ThreadPoolExecutor
        q_lower = query.lower()
        tmdb = get_tmdb()

        # Run title search + person search in parallel
        def _title_search():
            return tmdb.search_multi_results(query, page=1, limit=40, pages=2)

        def _person_search():
            return tmdb.search_person(query, limit=8)

        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_title  = ex.submit(_title_search)
            fut_person = ex.submit(_person_search)
            raw        = fut_title.result()
            persons    = fut_person.result()

        # Re-rank titles by relevance
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

        # Serialize people results
        people = []
        for p in persons:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            known = [kf.get("title") or kf.get("name") or "" for kf in (p.get("known_for") or [])[:3]]
            people.append({
                "id": p.get("id"),
                "name": name,
                "known_for_department": p.get("known_for_department", "Acting"),
                "profile_url": (f"https://image.tmdb.org/t/p/w185{p['profile_path']}"
                                if p.get("profile_path") else ""),
                "popularity": float(p.get("popularity", 0)),
                "known_for": [k for k in known if k],
            })

        return jsonify({"ok": True, "results": results, "people": people})
    except RuntimeError:
        return jsonify({"ok": True, "results": [], "people": []})


@app.route("/api/person/<int:person_id>", methods=["GET", "OPTIONS"])
@api_login_required
def api_person_detail(person_id: int) -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        data = get_tmdb().get_person_details(person_id)
        if not data:
            return jsonify({"ok": False, "error": "Person not found"}), 404
        return jsonify({"ok": True, "person": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


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
        watched_keys, _ = db.get_status_keys(user_id)
        results = []
        for result in raw:
            movie = build_movie_from_tmdb(result, {}, None, result.get("media_type", CONTENT_TYPE_MOVIE))
            item = serialize_movie(movie)
            item["is_watched"] = (movie.tmdb_id, movie.content_type) in watched_keys
            results.append(item)
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
    try:
        genre_map = tmdb.get_genres(content_type)
    except RuntimeError:
        genre_map = {}
    genre_id_to_name = {v: k for k, v in genre_map.items()}
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
# What people type → the names TMDB uses, in order of preference. Which one exists
# depends on the user's region ("hotstar" is JioHotstar in India, Disney Plus elsewhere).
_PRIME = ["Amazon Prime Video"]
_HOTSTAR = ["JioHotstar", "Disney+ Hotstar", "Hotstar", "Disney Plus"]
_DISNEY = ["Disney Plus", "Disney+", "JioHotstar"]
_SONY = ["Sony Liv", "SonyLIV"]
_APPLE = ["Apple TV", "Apple TV Plus", "Apple TV+"]
_PARAMOUNT = ["Paramount Plus", "Paramount Plus Premium", "Paramount+"]
_PLATFORM_ALIASES: dict[str, list[str]] = {
    "netflix": ["Netflix"],
    "prime": _PRIME, "prime video": _PRIME, "amazon prime": _PRIME, "amazon": _PRIME,
    "hotstar": _HOTSTAR, "jiohotstar": _HOTSTAR, "jio hotstar": _HOTSTAR,
    "disney": _DISNEY, "disney+": _DISNEY, "disney plus": _DISNEY,
    "sonyliv": _SONY, "sony liv": _SONY, "sony": _SONY,
    "zee5": ["Zee5"], "zee": ["Zee5"],
    "jiocinema": ["JioCinema", "JioHotstar"], "jio": ["JioHotstar", "JioCinema"],
    "apple tv": _APPLE, "apple tv+": _APPLE, "apple": _APPLE,
    "mubi": ["MUBI"],
    "sun nxt": ["Sun Nxt"], "sunnxt": ["Sun Nxt"],
    "hulu": ["Hulu"],
    "hbo": ["HBO Max", "Max"], "hbo max": ["HBO Max", "Max"],
    "peacock": ["Peacock Premium", "Peacock"],
    "paramount": _PARAMOUNT, "paramount+": _PARAMOUNT, "paramount plus": _PARAMOUNT,
    "crunchyroll": ["Crunchyroll"],
    "iplayer": ["BBC iPlayer"], "bbc": ["BBC iPlayer"],
    "lionsgate": ["Lionsgate Play"],
    "mx player": ["MX Player", "Amazon MX Player"],
    "youtube": ["YouTube Premium", "YouTube"],
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
    "feel-good": ["Comedy", "Family"],
    "feel good": ["Comedy", "Family"],
    "mind-bending": ["Science Fiction", "Thriller", "Mystery"],
    "mind bending": ["Science Fiction", "Thriller", "Mystery"],
    "whodunit": ["Mystery", "Crime"],
    "tearjerker": ["Drama", "Romance"],
    "spooky": "Horror",
    "hilarious": "Comedy",
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


def _term_pattern(term: str, plural: bool = False) -> str:
    """Regex for `term` as a whole word or phrase; `plural` also accepts "thrillers" / "comedies"."""
    term = term.lower()
    core = re.escape(term)
    if plural:
        core = f"(?:{core}|{re.escape(term[:-1])}ies)" if term.endswith("y") else f"{core}(?:e?s)?"
    return rf"(?<![a-z0-9]){core}(?![a-z0-9])"


def _has_term(text: str, term: str, plural: bool = False) -> bool:
    """True when `term` appears in `text` as a whole word or phrase (`text` must be lowercase).
    Plain substring checks made "war" fire inside "award", "malay" inside "malayalam" and
    "doc" inside "doctor strange"."""
    return re.search(_term_pattern(term, plural), text) is not None


# Words that flip a nearby genre mention from "wanted" to "avoid" —
# e.g. "which are not animated", "but not horror", "excluding romance".
_NEGATION = re.compile(
    r"\b(?:not|no|never|nothing|except|excluding|without|besides|avoid|skip|minus)\b|n't",
    re.IGNORECASE,
)
_NEGATION_WINDOW = 30  # chars scanned immediately before a genre mention


def _extract_exclude_genres(message: str, genre_options: list[str]) -> list[str]:
    """Find genres the user explicitly wants to AVOID, e.g. 'movies like Pokiri which
    are not animated' → ['Animation']. Runs without any API calls, so it works even
    when the LLM extraction is unavailable or misses the exclusion.
    """
    msg = message.lower()
    genre_set = set(genre_options)
    alias_map: dict[str, str | list[str]] = dict(_GENRE_ALIASES)
    for g in genre_options:
        alias_map.setdefault(g.lower(), g)

    excluded: list[str] = []
    for alias in sorted(alias_map, key=len, reverse=True):
        for match in re.finditer(_term_pattern(alias, plural=True), msg):
            # Only look back within the same clause: in "not too long, a comedy" the
            # "not" belongs to the clause before the comma.
            clause_start = max(msg.rfind(sep, 0, match.start()) for sep in (",", ".", ";", "!", "?", " but "))
            window = msg[max(clause_start, match.start() - _NEGATION_WINDOW, 0):match.start()]
            # "non-" only negates the word it is attached to ("non-animated family films").
            attached_non = msg[:match.start()].endswith(("non-", "non "))
            if not (attached_non or _NEGATION.search(window)):
                continue
            canonical = alias_map[alias]
            for name in ([canonical] if isinstance(canonical, str) else canonical):
                if name in genre_set and name not in excluded:
                    excluded.append(name)
    return excluded


def _fuzzy_match_provider(name: str, provider_options: list[str]) -> str | None:
    """Resolve what a person (or the model) called a platform to the region's exact name."""
    name_lower = name.lower().strip()
    if not name_lower:
        return None
    by_lower = {opt.lower(): opt for opt in provider_options}
    # 1. Exact name
    if name_lower in by_lower:
        return by_lower[name_lower]
    # 2. Known alias → first candidate that exists in this region
    for candidate in _PLATFORM_ALIASES.get(name_lower, []):
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    # 3. Whole-word containment either way. Prefer a real platform over its add-on
    #    channels, then the shortest name ("Apple TV", not "AMC Plus Apple TV channel").
    hits = [
        opt for opt in provider_options
        if _has_term(opt.lower(), name_lower) or _has_term(name_lower, opt.lower())
    ]
    hits = [h for h in hits if not _PROVIDER_NOISE.search(h)] or hits
    return min(hits, key=len) if hits else None


_TV_WORDS = re.compile(
    r"\b(?:tv|shows|series|sitcoms?|episodes?|k-?dramas?|miniseries"
    r"|(?:a|new|good|great|best|any|some|this|that)\s+show)\b"
)
_MOVIE_WORDS = re.compile(r"\b(?:movies?|films?|flicks?)\b")
# "my watch history" is about the user's library, not the History genre.
_OWN_HISTORY = re.compile(r"\b(?:watch(?:ed)?|viewing|my|chat|search)\s+history\b")
_PLATFORM_CUE = r"\b(?:on|via|from|at|using)\s+"


def _extract_filters_keyword(
    user_message: str,
    genre_options: list[str],
    provider_options: list[str],
    language_options: list[str],
) -> dict:
    """Keyword-based filter extraction — no API calls. Fills gaps the model leaves and
    carries the whole chat when the model is unavailable. Everything matches on whole
    words, never substrings.
    """
    msg = user_message.lower()
    own_history = bool(_OWN_HISTORY.search(msg))

    # Genre — exact names, then aliases ("sci-fi" → "Science Fiction"), longest first.
    genres: list[str] = []
    genre_set = set(genre_options)
    for g in genre_options:
        if g == "History" and own_history:
            continue
        if _has_term(msg, g, plural=True) and g not in genres:
            genres.append(g)
    for alias in sorted(_GENRE_ALIASES, key=len, reverse=True):
        if alias in ("history", "historical") and own_history:
            continue
        if _has_term(msg, alias, plural=True):
            canonical = _GENRE_ALIASES[alias]
            for name in ([canonical] if isinstance(canonical, str) else canonical):
                if name in genre_set and name not in genres:
                    genres.append(name)

    # Platform — aliases, then the region's own provider names. Short single-word names
    # ("aha", "Stan", "WOW", "BINGE") are everyday words too, so they need a cue ("on aha").
    platforms: list[str] = []
    for alias in sorted(_PLATFORM_ALIASES, key=len, reverse=True):
        if _has_term(msg, alias):
            matched = _fuzzy_match_provider(alias, provider_options)
            if matched and matched not in platforms:
                platforms.append(matched)
    for opt in provider_options:
        name = opt.lower()
        if opt in platforms or _PROVIDER_NOISE.search(opt):
            continue
        distinctive = " " in name or len(name) >= 6 or any(ch.isdigit() for ch in name)
        if distinctive:
            hit = _has_term(msg, name)
        else:
            hit = re.search(rf"{_PLATFORM_CUE}{re.escape(name)}(?![a-z0-9])", msg) is not None
        if hit:
            platforms.append(opt)

    # Language — colloquial names first ("tollywood"), then the language list itself.
    languages: list[str] = []
    language_set = set(language_options)
    for alias, canonical in _LANGUAGE_ALIASES.items():
        if _has_term(msg, alias) and canonical in language_set and canonical not in languages:
            languages.append(canonical)
    for name in language_options:
        if len(name) >= 4 and name not in languages and _has_term(msg, name.lower()):
            languages.append(name)

    # Content type — only when the message says one and not the other. Platform names
    # are blanked first: the "TV" in "Apple TV" is not a request for a TV show.
    plain = msg
    for term in [name.lower() for name in platforms] + [a for a in _PLATFORM_ALIASES if _has_term(msg, a)]:
        plain = plain.replace(term, " ")
    says_movie = bool(_MOVIE_WORDS.search(plain))
    says_tv = bool(_TV_WORDS.search(plain))
    content_type = CONTENT_TYPE_MOVIE if says_movie and not says_tv else CONTENT_TYPE_TV if says_tv and not says_movie else ""

    # Exclusions — "which are not animated", "but not horror" etc. A negated genre is
    # not also wanted (the passes above pick up "animated" → Animation either way).
    exclude_genres = _extract_exclude_genres(user_message, genre_options)
    genres = [g for g in genres if g not in exclude_genres]

    return {
        "genres": genres, "platforms": platforms, "languages": languages,
        "content_type": content_type, "exclude_genres": exclude_genres,
    }


# Phrases that are clearly conversational with zero search intent
_GREETINGS = {"hi", "hello", "hey", "hiya", "howdy", "sup", "yo", "greetings", "good morning",
               "good afternoon", "good evening", "good night", "thanks", "thank you", "ty",
               "cool", "ok", "okay", "great", "nice", "awesome", "sounds good", "bye", "goodbye"}
# First words that open small talk ("hey there", "thanks a lot", "ok cool").
_GREETING_OPENERS = {"hi", "hello", "hey", "hiya", "howdy", "sup", "yo", "greetings", "thanks",
                     "thank", "ty", "cool", "ok", "okay", "great", "nice", "awesome", "bye", "goodbye"}

_SEARCH_KEYWORDS = {
    "watch", "movie", "movies", "show", "shows", "film", "films", "series", "tv",
    "recommend", "suggest", "find", "get", "like", "similar", "best", "top", "surprise",
    "good", "thriller", "drama", "comedy", "action", "horror", "sci-fi", "anime",
    "netflix", "prime", "hotstar", "platform", "genre", "language",
    "telugu", "hindi", "tamil", "english", "kannada", "malayalam",
}


def _has_search_vocab(msg: str) -> bool:
    return any(_has_term(msg, word) for word in _SEARCH_KEYWORDS)


def _is_general_message(message: str, genres: list, platforms: list, languages: list) -> bool:
    """Keyword-only judgement that a message has no search intent (used when the model
    is unavailable)."""
    if _is_obvious_greeting(message):
        return True
    msg = re.sub(r"[!?.,\s]+$", "", message.lower().strip())
    # Short message with nothing extracted and no search vocabulary
    return len(msg.split()) <= 4 and not (genres or platforms or languages) and not _has_search_vocab(msg)


def _build_general_reply(message: str, taste_profile: dict) -> str:
    """Craft a warm, personalised conversational reply when there's no search intent."""
    msg = message.lower().strip().rstrip("!?.,")
    top_genres = taste_profile.get("top_genres", [])
    recent = taste_profile.get("recent_watched", [])

    # Farewells
    if any(_has_term(msg, w) for w in ("bye", "goodbye", "see you", "later")):
        return "See you! Come back whenever you need a good watch recommendation. 🎬"

    # Thanks
    if any(_has_term(msg, w) for w in ("thanks", "thank you", "ty", "great", "awesome", "cool", "nice", "sounds good")):
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


# Words that end a reference title and start a trailing qualifier/exclusion clause —
# e.g. "movies like Pokiri which are not animated" should stop capturing at "which".
_TITLE_BOUNDARY_WORDS = r"but|on|in|with|please|and|for|which|that|except|excluding|without|besides"


_RUNTIME_CUE = r"\b(?:under|below|less than|shorter than|within|at most|no longer than|no more than|up to|max(?:imum)?(?: of)?)\s+"
_RUNTIME_HOURS = re.compile(_RUNTIME_CUE + r"(\d+(?:\.\d+)?|an?|one|two|three)\s*(?:hours?|hrs?)\b")
_RUNTIME_MINUTES = re.compile(_RUNTIME_CUE + r"(\d{2,3})\s*(?:minutes?|mins?)\b")
_HOUR_WORDS = {"a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0}


def _extract_runtime_max(message: str) -> int | None:
    """Longest running time asked for, in minutes: "under 2 hours" → 120."""
    msg = message.lower()
    hours = _RUNTIME_HOURS.search(msg)
    if hours:
        minutes = int(round((_HOUR_WORDS.get(hours.group(1)) or float(hours.group(1))) * 60))
    else:
        mins = _RUNTIME_MINUTES.search(msg)
        if not mins:
            return None
        minutes = int(mins.group(1))
    return minutes if 45 <= minutes <= 400 else None


_TITLE_NOUN = r"(?:movies?|films?|flicks?|shows?|series|releases?|titles?|dramas?)"
_DECADE = re.compile(r"\b(?:(19|20)(\d)0|(\d)0)'?s\b")
_YEAR_BOUND = re.compile(r"\b(after|since|post|before|until|pre|from|in|of)[\s-]+((?:19|20)\d{2})\b")
_RECENT = re.compile(rf"\b(?:recent(?:ly released)?|latest|newest|new releases?|new\s+(?:\w+\s+)?{_TITLE_NOUN})\b")
_VINTAGE = re.compile(rf"\b(?:classics?|old|older|vintage)\s+(?:\w+\s+)?{_TITLE_NOUN}\b|\bclassics\b")


def _extract_year_range(message: str) -> tuple[int | None, int | None]:
    """The era a request names, by the same rules the model is given: "90s" → 1990–1999,
    "from 2015" → that year, "after 2020" → 2021 onwards, "recent" → last year and this."""
    msg = message.lower()
    this_year = date.today().year
    decade = _DECADE.search(msg)
    if decade:
        if decade.group(1):
            start = int(decade.group(1) + decade.group(2) + "0")
        else:
            start = (1900 if int(decade.group(3)) >= 3 else 2000) + int(decade.group(3)) * 10
        return start, start + 9
    bound = _YEAR_BOUND.search(msg)
    if bound and 1900 <= int(bound.group(2)) <= this_year + 1:
        word, year = bound.group(1), int(bound.group(2))
        if word in ("after", "since", "post"):
            return (year if word == "since" else year + 1), None
        if word in ("before", "until", "pre"):
            return None, (year if word == "until" else year - 1)
        return year, year
    if _RECENT.search(msg):
        return this_year - 1, this_year
    if _VINTAGE.search(msg):
        return None, 1989
    return None, None


def _within_runtime(tmdb: TMDBClient, region: str, movies: list[Movie], runtime_max: int | None, limit: int = 24) -> list[Movie]:
    """Keep the titles no longer than `runtime_max` minutes. Lists that don't come from
    discover (a person's credits, "viewers also liked") carry no runtime, so each title
    is looked up — in parallel, and cached like every other detail call."""
    if not runtime_max or not movies:
        return movies
    movies = movies[:limit]
    details = tmdb.map_parallel(
        lambda m: tmdb.get_content_details(m.tmdb_id, m.content_type, region=region), movies
    )
    return [
        m for m, d in zip(movies, details)
        if d is not None and 0 < int(d.info.get("runtime_minutes") or 0) <= runtime_max
    ]


def _recs_within_runtime(tmdb: TMDBClient, region: str, recs: list[Recommendation], runtime_max: int | None, limit: int = 30) -> list[Recommendation]:
    """The same check for ranked results. Discover's own runtime filter is applied first,
    but its index is occasionally out of date, so the shortlist is confirmed title by title."""
    if not runtime_max or not recs:
        return recs
    recs = recs[:limit]
    fits = {
        (m.content_type, m.tmdb_id)
        for m in _within_runtime(tmdb, region, [r.movie for r in recs], runtime_max, limit=limit)
    }
    return [r for r in recs if (r.movie.content_type, r.movie.tmdb_id) in fits]


def _extract_reference_title(message: str) -> str | None:
    """Extract a movie/show title from patterns like 'movies like Pokiri' or 'similar to Interstellar'.
    Stops at a trailing qualifier clause ("which are not animated", "but not horror") so that
    clause never ends up glued onto the title itself.
    """
    import re
    patterns = [
        rf"(?:movies?|shows?|films?|series|something|more)\s+like\s+['\"]?([^,?.!\n\"']+?)['\"]?(?:\s+(?:{_TITLE_BOUNDARY_WORDS})\b|\s*$)",
        rf"similar\s+to\s+['\"]?([^,?.!\n\"']+?)['\"]?(?:\s+(?:{_TITLE_BOUNDARY_WORDS})\b|\s*$)",
        rf"(?:^|get me |find me |recommend (?:me )?|suggest )(?:movies?|shows?|films?)?\s*like\s+['\"]?([^,?.!\n\"']+?)['\"]?(?:\s+(?:{_TITLE_BOUNDARY_WORDS})\b|\s*$)",
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


def _strip_trailing_qualifier(title: str) -> str:
    """Cut a title off at the first qualifier/negation word it still contains
    ('which', 'that', 'not', 'except', ...). Safety net for when the LLM or the
    primary regex still glued an exclusion clause onto the title."""
    import re
    match = re.search(rf"\b(?:{_TITLE_BOUNDARY_WORDS}|not|no)\b", title, re.IGNORECASE)
    return title[:match.start()].strip(" ,.-") if match else title


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
    """Small talk with no search intent ("hi", "thanks!", "ok cool") — answered without
    loading TMDB metadata or running the full agent."""
    msg = re.sub(r"[!?.,\s]+$", "", message.lower().strip())
    words = msg.split()
    if not words:
        return False
    if msg in _GREETINGS:
        return True
    return len(words) <= 4 and words[0] in _GREETING_OPENERS and not _has_search_vocab(msg)


# ── Groq (LLM) ────────────────────────────────────────────────────────────────
_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Tried in order; the second is used when the first is rate-limited, retired or down.
_GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"
_GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "").strip() or "openai/gpt-oss-20b"
# A per-minute limit clears within seconds and is worth waiting out. A spent daily
# allowance comes back as a wait of minutes: that model is skipped until then
# (model → time it may be called again) instead of costing a round trip per message.
_GROQ_MAX_WAIT_SECONDS = 15
_groq_cooloff: dict[str, float] = {}


def _call_groq_api(
    system: str,
    messages: list[dict],
    max_tokens: int = 800,
    json_schema: dict | None = None,
) -> str:
    """Call Groq's OpenAI-compatible chat endpoint and return the reply text, or "" when
    the model can't be reached — callers then fall back to keyword handling.

    With `json_schema` the output is constrained to that schema (the model cannot drift
    into prose, which plain JSON mode allowed mid-conversation); if a model rejects the
    schema, plain JSON mode is tried before moving to the next model."""
    import urllib.error
    import urllib.request

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return ""

    full_messages = [{"role": "system", "content": system}] + list(messages)
    formats: list[dict | None] = [None]
    if json_schema:
        formats = [
            {"type": "json_schema", "json_schema": {"name": "nextpick_request", "strict": True, "schema": json_schema}},
            {"type": "json_object"},
        ]

    def _attempt(model: str) -> tuple[str, float | None, bool]:
        """(reply, None, False) on success; ("", seconds, False) when rate-limited;
        ("", None, True) when nothing else is worth trying."""
        for response_format in formats:
            payload: dict = {
                "model": model,
                "messages": full_messages,
                "max_tokens": max_tokens,
                "temperature": 0.3 if json_schema else 0.7,
            }
            if model.startswith("openai/gpt-oss"):
                # Reasoning models: keep the hidden thinking short so it doesn't eat the budget.
                payload["reasoning_effort"] = "low"
            if response_format:
                payload["response_format"] = response_format

            req = urllib.request.Request(
                _GROQ_API_URL,
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    # Groq sits behind Cloudflare, which rejects urllib's default
                    # "Python-urllib/x.y" agent with 403 (error 1010).
                    "User-Agent": "NextPick/1.0 (movie-recommendation-app)",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = json.loads(resp.read())
                content = (raw["choices"][0]["message"].get("content") or "").strip()
                if content:
                    return content, None, False
                app.logger.warning("Groq model %s returned an empty reply", model)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:200]
                app.logger.warning("Groq call failed (%s, HTTP %s): %s", model, exc.code, detail)
                if exc.code == 401:
                    return "", None, True       # bad key — nothing else will work
                if exc.code == 400:
                    continue                    # format rejected / invalid output: try the looser format
                if exc.code == 429:
                    try:
                        return "", float(exc.headers.get("retry-after") or 0) or 5.0, False
                    except (TypeError, ValueError):
                        return "", 5.0, False
            except Exception as exc:
                app.logger.warning("Groq call failed (%s): %s", model, exc)
            break                               # anything but a 400: move on to the next model
        return "", None, False

    waits: dict[str, float] = {}
    for model in dict.fromkeys(m for m in (_GROQ_MODEL, _GROQ_FALLBACK_MODEL) if m):
        if _groq_cooloff.get(model, 0.0) > time.time():
            continue
        content, wait, stop = _attempt(model)
        if content or stop:
            return content
        if wait is not None:
            if wait > _GROQ_MAX_WAIT_SECONDS:
                _groq_cooloff[model] = time.time() + wait
            else:
                waits[model] = wait
    if waits:
        # Every usable model is over its per-minute limit, which clears within seconds:
        # a short wait and a real answer beat falling back to keyword guesses.
        model = min(waits, key=lambda name: waits[name])
        time.sleep(waits[model] + 0.25)
        return _attempt(model)[0]
    return ""


# Shape of the agent's answer, enforced by the API (see _call_chat_agent for meanings).
_NULLABLE_TEXT = {"type": ["string", "null"]}
_TEXT_LIST = {"type": "array", "items": {"type": "string"}}
_CHAT_AGENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": ["filter", "similarity", "movie_info", "watchlist_action", "user_stats", "general"]},
        "reference_title": _NULLABLE_TEXT,
        "person": _NULLABLE_TEXT,
        "topic": _NULLABLE_TEXT,
        "genres": _TEXT_LIST,
        "exclude_genres": _TEXT_LIST,
        "platforms": _TEXT_LIST,
        "languages": _TEXT_LIST,
        "content_type": {"type": "string", "enum": ["movie", "tv", ""]},
        "year_from": {"type": ["integer", "null"]},
        "year_to": {"type": ["integer", "null"]},
        "runtime_max": {"type": ["integer", "null"]},
        "watchlist_op": {"type": ["string", "null"], "enum": ["add", "remove", "watched", None]},
        "similar_titles": _TEXT_LIST,
        "reply": {"type": "string"},
    },
    "required": [
        "intent", "reference_title", "person", "topic", "genres", "exclude_genres", "platforms", "languages",
        "content_type", "year_from", "year_to", "runtime_max", "watchlist_op", "similar_titles", "reply",
    ],
}


_CHAT_INTENTS = {"filter", "similarity", "movie_info", "watchlist_action", "user_stats", "general"}
# Languages named in the prompt. Anything else the model returns still resolves against
# TMDB's full language list; sending all ~180 names would waste most of the token budget.
_CHAT_LANGUAGES = [
    "English", "Hindi", "Telugu", "Tamil", "Malayalam", "Kannada", "Bengali", "Marathi",
    "Punjabi", "Gujarati", "Urdu", "Korean", "Japanese", "Mandarin", "Cantonese", "French",
    "Spanish", "German", "Italian", "Portuguese", "Russian", "Turkish", "Arabic", "Thai",
    "Indonesian", "Swedish", "Danish", "Norwegian", "Dutch", "Polish",
]


def _plain_text(text: str) -> str:
    """The chat bubble shows raw text, so strip the markdown emphasis models like to add."""
    text = re.sub(r"(\*\*|__|`)", "", text or "")
    text = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)
    return text.strip()


def _profile_block(taste_profile: dict | None) -> str:
    lines: list[str] = []
    if taste_profile:
        if taste_profile.get("top_genres"):
            lines.append(f"- Favourite genres: {', '.join(taste_profile['top_genres'])}")
        if taste_profile.get("recent_watched"):
            lines.append(f"- Recently watched: {', '.join(taste_profile['recent_watched'][:6])}")
        if taste_profile.get("watchlist_titles"):
            lines.append(f"- On their watchlist: {', '.join(taste_profile['watchlist_titles'])}")
    return ("User's taste profile:\n" + "\n".join(lines)) if lines else "User's taste profile: nothing watched yet."


# Cards per reply. The chat panel shows this many (ChatBot.js), and the model is told
# about exactly these, so "the last one" means the last card the user can actually see.
CHAT_CARDS_SHOWN = 6


def _history_for_llm(history: list[dict], limit: int = 8) -> list[dict]:
    """Recent turns for the model. Assistant turns carry the titles that were shown as
    cards, so follow-ups like "tell me about the second one" can be resolved."""
    messages: list[dict] = []
    for item in history[-limit:]:
        content = (item.get("content") or "")[:400]
        if item.get("role") == "assistant" and item.get("recs_json"):
            try:
                shown = json.loads(item["recs_json"])[:CHAT_CARDS_SHOWN]
                titles = "; ".join(
                    f"{i}. {rec['movie']['title']}" + (f" ({rec['movie']['year']})" if rec["movie"].get("year") else "")
                    for i, rec in enumerate(shown, 1)
                )
                if titles:
                    content += f"\n[Titles shown to the user: {titles}]"
            except Exception:
                pass
        if content:
            messages.append({"role": "assistant" if item.get("role") == "assistant" else "user", "content": content})
    return messages


def _call_chat_agent(
    user_message: str,
    history: list[dict],
    genre_options: list[str],
    platform_names: list[str],
    language_options: list[str],
    taste_profile: dict | None,
    region_name: str,
) -> dict:
    """Ask the model what the user wants. Returns a normalised dict (see keys below),
    or {} when the model is unavailable so the caller can fall back to keywords."""
    today = date.today()
    language_names = [name for name in _CHAT_LANGUAGES if name in set(language_options)] or _CHAT_LANGUAGES

    system_prompt = f"""You are the brain of NextPick, a movie & TV recommendation app. Read the user's latest message (and the conversation so far) and return ONE JSON object describing what they want. The app runs the real search and shows result cards itself.

Today is {today.isoformat()}. The user is in {region_name}.
{_profile_block(taste_profile)}

Allowed genres: {json.dumps(genre_options)}
Platforms popular in {region_name}: {json.dumps(platform_names)}
Common languages: {json.dumps(language_names)}

Return exactly these keys:
{{"intent": "filter|similarity|movie_info|watchlist_action|user_stats|general", "reference_title": string or null, "person": string or null, "topic": string or null, "genres": [], "exclude_genres": [], "platforms": [], "languages": [], "content_type": "movie|tv|", "year_from": number or null, "year_to": number or null, "runtime_max": number or null, "watchlist_op": "add|remove|watched" or null, "similar_titles": [], "reply": string}}

INTENTS
- filter: wants recommendations by criteria — genre, mood, platform, language, era, or an actor/director. Also for vague asks ("what should I watch", "surprise me"): leave every filter empty — the app applies their taste by itself.
- similarity: wants titles LIKE a named title. reference_title = that title only. Also fill similar_titles with up to 10 well-known titles that are genuinely close in tone, themes and style — best match first, titles only — and that respect everything the user asked (movie vs show, exclusions, language, era). Leave similar_titles empty otherwise, except for a topic request (see topic). Any "like X" / "similar to X" / "in the vein of X" request is similarity, even across formats: "TV shows like Inception" -> similarity, reference_title "Inception", content_type "tv", similar_titles = TV series, genres [].
- movie_info: asks ABOUT one specific title (plot, cast, director, rating, runtime, where to watch, is it good). reference_title = the title. Set reply to "" — the app answers from its own database.
- watchlist_action: "add/save X to my watchlist" -> "add"; "remove X from my watchlist" -> "remove"; "I watched X" / "mark X as watched" -> "watched". reference_title = the title. reply "".
- user_stats: asks about their own history, taste or watchlist. Answer in reply using only the taste profile.
- general: greetings, thanks, opinions and film talk that needs no search. Answer in reply.

FIELDS
- Only fill a filter the user actually asked for. Never add genres, languages, platforms or years from the taste profile or from your own knowledge of a title or person: "Nolan films" sets person and nothing else; "best Hindi movies" sets the language and nothing else.
- genres / exclude_genres: only strings from Allowed genres. Map moods and slang: scary->Horror; funny->Comedy; feel-good->Comedy,Family; mind-bending->Science Fiction,Thriller,Mystery; sci-fi->Science Fiction; rom-com->Romance,Comedy; anime->Animation (and language Japanese); whodunit->Mystery,Crime; tearjerker->Drama,Romance.
- exclude_genres: what they want to avoid ("not animated", "no horror", "nothing scary"). Never put a genre in both lists.
- platforms: use the exact string from the platform list when the user names one (prime -> the Amazon Prime Video entry, hotstar -> the Hotstar entry). If it is not in the list, still return the name they used.
- languages: English names. Tollywood->Telugu, Bollywood->Hindi, Kollywood->Tamil, Mollywood->Malayalam, Sandalwood->Kannada, K-drama->Korean with content_type "tv".
- person: an actor, director or creator they ask for ("Mahesh Babu movies", "Nolan films", "something by Rajamouli") — the full name as best you know it. Otherwise null.
- topic: a franchise, character, fictional universe or subject they want titles FROM or ABOUT, when that is not a genre, a person or a "like X" request: "Batman movies" -> "Batman"; "Harry Potter films" -> "Harry Potter"; "Marvel shows" -> "Marvel"; "movies about time travel" -> "time travel"; "superhero movies" -> "superhero". The intent is filter. Also put up to 10 real titles that belong to it in similar_titles, best known first. Otherwise null.
- content_type: "movie" or "tv" only when the user says film/movie or show/series; otherwise "".
- year_from / year_to: only when they give an era. "90s" -> 1990 and 1999; "from 2015" -> 2015 and 2015; "recent", "new", "latest" -> {today.year - 1} and {today.year}; "classic", "old" -> null and 1989.
- runtime_max: longest length in minutes, only when they ask ("under 2 hours" -> 120, "90 minutes or less" -> 90, "something short" -> 100); otherwise null.
- reference_title: fix obvious typos; never include a qualifier such as "which are not animated" (that goes in exclude_genres).
- Follow-ups build on the conversation. "on Netflix instead", "something lighter", "more like that": keep the earlier criteria and change only what they ask. If the request being narrowed was a "like X" request ("only the ones on Netflix", "newer ones", "but as a series"), it is STILL similarity: repeat the same reference_title, give similar_titles again, and add the new constraint. "the second one", "that movie", "it" refer to the numbered titles in "[Titles shown to the user: ...]" or the title just discussed — put the actual title in reference_title.

REPLY
- filter / similarity: ONE short, warm lead-in sentence, at most 25 words. Do NOT name any specific title and do NOT claim anything is available — the app lists the real results under your sentence.
- general / user_stats: 1 to 3 friendly sentences.
- Plain text only: no markdown, asterisks or lists. Never mention JSON, intents, or that you are searching."""

    messages = _history_for_llm(history)
    # The closing reminder keeps the smaller fallback model from answering a direct
    # question in prose, which the API rejects as invalid JSON.
    messages.append({"role": "user", "content": f"{user_message}\n\n(Answer with the JSON object only.)"})

    text = _call_groq_api(system_prompt, messages, max_tokens=1200, json_schema=_CHAT_AGENT_SCHEMA)
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except Exception:
        found = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            raw = json.loads(found.group()) if found else None
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        return {}

    def text_of(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    def list_of(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        return [v.strip() for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []

    def year_of(value: Any) -> int | None:
        try:
            year = int(value)
        except (TypeError, ValueError):
            return None
        return year if 1900 <= year <= today.year + 1 else None

    intent = text_of(raw.get("intent")).lower()
    content_type = text_of(raw.get("content_type")).lower()
    watchlist_op = text_of(raw.get("watchlist_op")).lower()
    year_from, year_to = year_of(raw.get("year_from")), year_of(raw.get("year_to"))
    if year_from and year_to and year_from > year_to:
        year_from, year_to = year_to, year_from
    try:
        runtime_max = int(raw.get("runtime_max"))
        runtime_max = runtime_max if 45 <= runtime_max <= 400 else None
    except (TypeError, ValueError):
        runtime_max = None
    return {
        "intent": intent if intent in _CHAT_INTENTS else "filter",
        "reference_title": text_of(raw.get("reference_title")),
        "person": text_of(raw.get("person")),
        "topic": text_of(raw.get("topic")),
        "genres": list_of(raw.get("genres")),
        "exclude_genres": list_of(raw.get("exclude_genres")),
        "platforms": list_of(raw.get("platforms")),
        "languages": list_of(raw.get("languages")),
        "content_type": content_type if content_type in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV) else "",
        "year_from": year_from,
        "year_to": year_to,
        "runtime_max": runtime_max,
        "watchlist_op": watchlist_op if watchlist_op in ("add", "remove", "watched") else "",
        "similar_titles": list_of(raw.get("similar_titles"))[:10],
        "reply": _plain_text(text_of(raw.get("reply"))),
    }


def _call_chat_smalltalk(user_message: str, history: list[dict], taste_profile: dict | None) -> str:
    """Short conversational reply for greetings and thanks."""
    system = f"""You are NextPick's friendly movie & TV assistant — warm and concise.
{_profile_block(taste_profile)}

The user is making small talk. Reply in 1 or 2 natural sentences and invite them to say what they feel like watching (a mood, genre, platform, or a title they loved). Do not recommend specific titles here. Plain text only."""
    messages = _history_for_llm(history, limit=4)
    messages.append({"role": "user", "content": user_message})
    return _plain_text(_call_groq_api(system, messages, max_tokens=400)) or _build_general_reply(user_message, taste_profile or {})


def _canonical_names(values: list[str], options: list[str], aliases: dict | None = None) -> list[str]:
    """Map names from the model onto the app's exact option strings (case-insensitive,
    with an optional alias table)."""
    by_lower = {opt.lower(): opt for opt in options}
    resolved: list[str] = []
    for value in values:
        key = value.lower()
        candidates: list[str] = []
        if key in by_lower:
            candidates = [by_lower[key]]
        elif aliases and key in aliases:
            target = aliases[key]
            candidates = [target] if isinstance(target, str) else list(target)
        for name in candidates:
            if name in by_lower.values() and name not in resolved:
                resolved.append(name)
    return resolved


def _with_tv_genres(genres: list[str] | set[str]) -> set[str]:
    """Genre names plus their TV equivalents ("Action" also matches "Action & Adventure"),
    so filtering works on shows as well as films."""
    expanded = set(genres)
    expanded.update(TV_GENRE_ALIASES[g] for g in genres if g in TV_GENRE_ALIASES)
    return expanded


# Said instead of "nothing matched" or "check the spelling" when the real reason a
# lookup came back empty is that TMDB could not be reached.
_TMDB_DOWN_REPLY = (
    "I can't reach the movie database right now, so I can't look anything up. "
    "Please check your connection and try again in a minute."
)


def _shown_cards(history: list[dict]) -> dict[str, dict]:
    """Titles on the cards of recent replies → that card's TMDB identity, newest reply
    winning. A title the user points at ("the third one") is then exactly that card, not
    whichever same-named title a search happens to rank first."""
    shown: dict[str, dict] = {}
    for item in history:
        if item.get("role") != "assistant" or not item.get("recs_json"):
            continue
        try:
            recs = json.loads(item["recs_json"])[:CHAT_CARDS_SHOWN]
        except Exception:
            continue
        for rec in recs:
            movie = rec.get("movie") or {}
            if movie.get("title") and movie.get("tmdb_id"):
                shown[movie["title"].lower()] = {
                    "media_type": movie.get("content_type") or CONTENT_TYPE_MOVIE,
                    "id": movie["tmdb_id"],
                }
    return shown


def _find_title(tmdb: TMDBClient, title: str, shown: dict[str, dict] | None = None) -> dict | None:
    """Best TMDB match for a title someone typed, retrying without a trailing qualifier.
    A title already on a card in this chat (`shown`) is that card, with no search."""
    if shown:
        card = shown.get(re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip().lower())
        if card:
            return card
    for candidate in dict.fromkeys([title, _strip_trailing_qualifier(title)]):
        if not candidate:
            continue
        results = tmdb.search_multi_results(candidate, page=1, limit=5)
        if results:
            return results[0]
    return None


def _card(movie: Movie) -> dict[str, Any]:
    return serialize_recommendation(Recommendation(movie=movie, score=0.0, reasons=[]))


# "the second one", "the last film", "number 3" — a position in the list shown before.
# The agent has already turned it into a title, so the answering model is given the
# title itself; left in, the words read as a question about a sequel.
_LIST_POSITION = re.compile(
    r"\b(?:the\s+)?(?:(?P<ordinal>first|second|third|fourth|fifth|sixth|last|1st|2nd|3rd|[4-6]th)\s+"
    r"(?:one|movie|film|show|series|title|pick|option|result|suggestion)|(?:number|no\.?)\s*(?P<number>[1-9]))\b",
    re.IGNORECASE,
)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6}


def _card_at_position(message: str, history: list[dict]) -> dict | None:
    """The card a message points at by position ("the second one", "the last film",
    "number 4") in the most recent list of cards. Worked out here rather than left to
    the model, which miscounts now and then."""
    match = _LIST_POSITION.search(message)
    if not match:
        return None
    for item in reversed(history):
        if item.get("role") != "assistant" or not item.get("recs_json"):
            continue
        try:
            cards = [rec.get("movie") or {} for rec in json.loads(item["recs_json"])[:CHAT_CARDS_SHOWN]]
        except Exception:
            continue
        if len(cards) < 2:
            continue                    # a single card is an answer about one title, not a list
        ordinal = (match.group("ordinal") or "").lower()
        position = len(cards) if ordinal == "last" else _ORDINALS.get(ordinal) or int(match.group("number") or 0)
        card = cards[position - 1] if 1 <= position <= len(cards) else None
        return card if card and card.get("title") else None
    return None


def _answer_about_title(tmdb: TMDBClient, region: str, title: str, question: str,
                        shown: dict[str, dict] | None = None) -> tuple[str, list[dict]]:
    """Answer a question about one title from TMDB's data, not the model's memory —
    the model only phrases the facts. Returns (reply, [card for the title])."""
    item = _find_title(tmdb, title, shown)
    if not item:
        return f"I couldn't find “{title}” on TMDB — could you check the spelling?", []
    content_type = item.get("media_type", CONTENT_TYPE_MOVIE)
    details = tmdb.get_content_details(int(item.get("id", 0)), content_type, region=region)
    movie, info = details.movie, details.info
    region_name = SUPPORTED_REGIONS[region]
    platforms = [name for name in details.providers if not _PROVIDER_DUPLICATE.search(name)]

    facts = [
        f"Title: {movie.title}",
        f"Type: {content_label(content_type)}",
        f"Released: {info.get('release_date') or movie.year or 'unknown'}",
        f"Genres: {', '.join(details.genres) or 'unknown'}",
        f"TMDB rating: {details.original_rating:.1f}/10",
        f"Original language code: {movie.language or 'unknown'}",
    ]
    if info.get("directors"):
        facts.append(f"Directed by: {', '.join(info['directors'])}")
    if info.get("creators"):
        facts.append(f"Created by: {', '.join(info['creators'])}")
    if details.cast:
        facts.append(f"Main cast: {', '.join(details.cast[:8])}")
    if info.get("runtime_minutes"):
        facts.append(f"Runtime: {info['runtime_minutes']} minutes")
    if info.get("seasons"):
        facts.append(f"Seasons: {info['seasons']} ({info.get('episodes') or '?'} episodes), status: {info.get('status') or 'unknown'}")
    if details.certification:
        facts.append(f"Age rating in {region_name}: {details.certification}")
    facts.append(f"Where to watch in {region_name}: {', '.join(platforms[:8]) if platforms else 'no streaming information available'}")
    if info.get("tagline"):
        facts.append(f"Tagline: {info['tagline']}")
    facts.append(f"Overview: {movie.overview or 'not available'}")

    system = (
        "You are NextPick's movie & TV assistant. Answer the user's question about the title below "
        "using ONLY these facts. 2 to 4 warm, natural sentences; no bullet lists; no spoilers beyond "
        "the overview. If the facts don't cover what they asked, say you don't have that detail "
        "rather than guessing. Never invent cast, dates, awards or availability. Whatever words the "
        "user used (\"it\", \"that one\", \"the second one\"), they simply mean this title, so don't comment on those words. Plain text only — "
        "no markdown, asterisks or headings.\n\nFACTS\n" + "\n".join(facts)
    )
    asked = _LIST_POSITION.sub(lambda _: f"“{movie.title}”", question)
    reply = _plain_text(_call_groq_api(
        system, [{"role": "user", "content": f"About “{movie.title}”: {asked}"}], max_tokens=700,
    ))
    if not reply:
        # Model unavailable: a plain summary straight from the facts.
        parts = [f"{movie.title}" + (f" ({movie.year})" if movie.year else "") + f" is a {', '.join(details.genres[:3]).lower() or content_label(content_type).lower()} "
                 f"{content_label(content_type).lower()} rated {details.original_rating:.1f}/10 on TMDB."]
        if info.get("directors"):
            parts.append(f"Directed by {', '.join(info['directors'])}.")
        elif info.get("creators"):
            parts.append(f"Created by {', '.join(info['creators'])}.")
        if details.cast:
            parts.append(f"It stars {', '.join(details.cast[:4])}.")
        if platforms:
            parts.append(f"In {region_name} you can watch it on {', '.join(platforms[:4])}.")
        reply = " ".join(parts)
    return reply, [_card(movie)]


# Watchlist commands with an obvious shape, for when the model can't be asked. (Without
# these, "add Kantara to my watchlist" was answered with a list of random picks.)
_KW_ADD = re.compile(r"\b(?:add|save|put)\s+(.+?)\s+(?:to|on|in)\s+(?:my\s+)?watch\s?list\b", re.IGNORECASE)
_KW_REMOVE = re.compile(r"\b(?:remove|delete|drop|take)\s+(.+?)\s+(?:from|off)\s+(?:my\s+)?watch\s?list\b", re.IGNORECASE)
_KW_WATCHED = re.compile(
    r"^\s*(?:i(?:'ve|\s+have)?\s+(?:just\s+|already\s+)?(?:watched|seen|finished)|mark)\s+(.+?)"
    r"(?:\s+as\s+(?:watched|seen))?(?:\s+(?:yesterday|today|tonight|last night|last week|already|recently))?\s*[.!]*$",
    re.IGNORECASE,
)
_PRONOUN_TITLES = {"it", "that", "this", "that one", "this one"}


def _keyword_library_command(message: str, history: list[dict]) -> tuple[str, str] | None:
    """(operation, title) for a plainly worded watchlist / watched command, else None.
    "it" and "that one" mean the title the previous reply was about."""
    for pattern, operation in ((_KW_ADD, "add"), (_KW_REMOVE, "remove"), (_KW_WATCHED, "watched")):
        match = pattern.search(message)
        if not match:
            continue
        title = match.group(1).strip(" \"'“”‘’")
        if operation == "watched" and (len(title.split()) > 7 or re.search(r"\b(?:and|but)\b", title, re.IGNORECASE)):
            continue        # "I watched X and want something similar" is a search, not a command
        if title.lower() in _PRONOUN_TITLES:
            # Only when the reply just before this was about exactly one title; otherwise
            # the title is left empty and the user is asked which one they mean.
            title = ""
            previous = next((item for item in reversed(history) if item.get("role") == "assistant"), None)
            if previous and previous.get("recs_json"):
                try:
                    cards = json.loads(previous["recs_json"])
                except Exception:
                    cards = []
                if len(cards) == 1:
                    title = (cards[0].get("movie") or {}).get("title") or ""
        return operation, title
    return None


def _apply_library_action(
    db: Database, tmdb: TMDBClient, user_id: int, region: str, title: str, operation: str,
    shown: dict[str, dict] | None = None,
) -> tuple[str, bool]:
    """Add/remove a title on the watchlist, or mark it watched. Returns (reply, changed)."""
    if not title:
        return "Which title should I update? Tell me its name.", False
    item = _find_title(tmdb, title, shown)
    if not item:
        return f"I couldn't find “{title}” on TMDB — could you check the spelling?", False
    content_type = item.get("media_type", CONTENT_TYPE_MOVIE)
    tmdb_id = int(item.get("id", 0))
    movie = tmdb.get_content_details(tmdb_id, content_type, region=region).movie
    label = movie.title + (f" ({movie.year})" if movie.year else "")

    if operation == "remove":
        if not db.is_in_watchlist(user_id, tmdb_id, content_type):
            return f"{label} isn't on your watchlist, so there's nothing to remove.", False
        db.remove_watchlist_item(user_id, tmdb_id, content_type)
        return f"Done — I removed {label} from your watchlist.", True
    if operation == "watched":
        if db.is_in_watched_history(user_id, tmdb_id, content_type):
            return f"{label} is already in your watched history.", False
        db.add_watched_item(user_id, movie)
        db.remove_watchlist_item(user_id, tmdb_id, content_type)   # a title can't be in both
        return f"Nice — I've marked {label} as watched. It'll shape your recommendations from here.", True
    if db.is_in_watchlist(user_id, tmdb_id, content_type):
        return f"{label} is already on your watchlist.", False
    db.add_watchlist_item(user_id, movie)
    return f"Added {label} to your watchlist.", True


# Appearances that aren't really "a film/show with this person".
_NON_STORY_GENRES = {"Talk", "News", "Reality", "Documentary"}


def _find_person(tmdb: TMDBClient, name: str) -> dict | None:
    """The TMDB person a name refers to: the best-known one whose name contains every
    word asked for, or failing that a well-known person TMDB matched under a nickname or
    another spelling ("SRK"). TMDB's search is fuzzy — "Batman" brings up Zal Batmanglij —
    so its top result alone is not taken on trust."""
    people = tmdb.search_person(name, limit=5)
    wanted = set(_name_tokens(name))
    for person in people:
        if wanted <= set(_name_tokens(person.get("name") or "")):
            return person
    if people and float(people[0].get("popularity") or 0) >= 3:
        return people[0]
    return None


def _person_titles(
    tmdb: TMDBClient,
    person: dict,
    genres_by_type: dict[str, dict[str, int]],
) -> tuple[str, list[Movie]]:
    """A person's best-known films/shows, most popular first: acting roles for actors,
    directing/creating/writing credits for everyone else."""
    is_actor = (person.get("known_for_department") or "Acting") == "Acting"
    credits = tmdb.get_person_credits(int(person.get("id", 0)), limit=120, crew_only=not is_actor)
    lookups = {
        ctype: {gid: gname for gname, gid in genres_by_type.get(ctype, {}).items()}
        for ctype in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)
    }
    titles: list[Movie] = []
    for item in credits:
        content_type = item.get("media_type")
        if content_type not in lookups or int(item.get("vote_count") or 0) < 20:
            continue
        if is_actor and "job" not in item:
            # Skip cameos, bit parts and one-off guest spots.
            if content_type == CONTENT_TYPE_MOVIE and int(item.get("order") or 0) > 12:
                continue
            if content_type == CONTENT_TYPE_TV and int(item.get("episode_count") or 0) < 3:
                continue
        movie = build_movie_from_tmdb(item, lookups[content_type], None, content_type)
        if is_actor and set(movie.genres) & _NON_STORY_GENRES:
            continue
        titles.append(movie)
    return person.get("name") or "", titles


def _keep(movie: Movie, *, content_types, genres, exclude, language_codes, year_from, year_to, skip_keys) -> bool:
    """Shared post-filter for candidate titles that didn't come from a filtered TMDB query."""
    if (movie.content_type, movie.tmdb_id) in skip_keys:
        return False
    if content_types and movie.content_type not in content_types:
        return False
    movie_genres = set(movie.genres)
    if exclude and exclude & movie_genres:
        return False
    if genres and not (genres & movie_genres):
        return False
    if language_codes and movie.language not in language_codes:
        return False
    if year_from and (not movie.year or movie.year < year_from):
        return False
    if year_to and (not movie.year or movie.year > year_to):
        return False
    return True


def _name_tokens(text: str) -> list[str]:
    """Lower-case words of a name or title, punctuation dropped."""
    return re.findall(r"[a-z0-9]+", text.lower())


# Words that say nothing about WHAT is wanted ("get me some good batman movies to watch").
_SUBJECT_STOPWORDS = frozenset("""
a an the some any all more few couple lot lots of to for from in on at by with about and or but not no non
i im me my we us our you your he she they it its this that these those there here
is are was were be been am do does did have has had can could would should will shall may might must
get give show find fetch list tell recommend suggest search pick play bring want wanna need like love
looking look see watch watching watched seen know surprise similar say says think feel feeling mood
taste history watchlist profile recommendation recommendations picks
movie movies film films flick flicks shows series serial serials sitcom sitcoms title titles tv television
cinema episode episodes season seasons content stuff thing things one ones something anything everything
good great best better top nice cool awesome amazing interesting fun popular trending famous hit hits
latest new newest recent old older classic classics underrated must please pls kindly thanks thank
now today tonight tomorrow weekend evening right just really very so too also only even
what which who whom whose when where why how
long short longer shorter big small little bit kind sort type types
hi hello hey ok okay yes yeah sure
""".split())
# Dropped only when nothing else is left: "movie night" is no subject, "time travel" is.
_SUBJECT_WEAK_WORDS = frozenset({"time", "day", "night"})
# A request for something to watch, as opposed to chat ("tell me a joke").
_SUBJECT_CUE = re.compile(
    r"\b(?:movies?|films?|flicks?|shows?|series|sitcoms?|titles?|watch|get me|give me|show me|find|fetch"
    r"|recommend|suggest|looking for|want|anything|something|list)\b"
)
# Where the subject of a request ends and its conditions begin ("... but not animated").
_SUBJECT_END = re.compile(r"\s*(?:[,;]|\b(?:but|except|excluding|without|which|not)\b|\bthat\s+(?:are|is|aren't|isn't)\b).*$")


def _extract_subject(
    message: str, genre_options: list[str], provider_options: list[str], language_options: list[str]
) -> tuple[str, str]:
    """What is left of a request once the request words and the filter words (genres,
    platforms, languages, eras, lengths) are taken out: "get me batman movies" → "batman".
    It may name a franchise, a character, a subject, a person or a title — `_resolve_subject`
    asks TMDB which. No network calls.

    Returns (bare, whole). `bare` is the leftover itself, "" when nothing meaningful
    remains. `whole` keeps the genre words that sat among it, for names that contain
    one — "star wars" (not "star" plus the War genre), "family guy" — and is "" when
    there were none."""
    text = message.lower().replace("’", "'").replace("‘", "'")
    if not (_SUBJECT_CUE.search(text) or len(text.split()) <= 3):
        return "", ""
    text = _SUBJECT_END.sub("", text)
    text = _RUNTIME_HOURS.sub(" ", _RUNTIME_MINUTES.sub(" ", text))
    text = re.sub(r"\b(?:(?:19|20)\d{2}|\d0)'?s?\b", " ", text)          # 1994, 2000s, 90s

    def leftover(source: str) -> str:
        words = [w.strip("'.-&") for w in re.findall(r"[a-z0-9][a-z0-9'&.\-]*", source.replace("'s ", " ").rstrip("?!. "))]
        words = [w for w in words if w and w.replace("'", "") not in _SUBJECT_STOPWORDS]
        if all(w in _SUBJECT_WEAK_WORDS for w in words):
            return ""
        subject = " ".join(words)
        return subject if 2 <= len(subject) and len(words) <= 4 else ""

    def without(source: str, terms: list[str]) -> str:
        for term in sorted(set(terms), key=len, reverse=True):
            if term.lower() in source:
                source = re.sub(_term_pattern(term, plural=True), " ", source)
        return source

    text = without(text, (
        list(_LANGUAGE_ALIASES) + list(_PLATFORM_ALIASES)
        + [name for name in language_options if len(name) >= 4]
        + [name for name in provider_options if not _PROVIDER_NOISE.search(name)]
    ))
    bare = leftover(without(text, list(genre_options) + list(_GENRE_ALIASES)))
    whole = leftover(text) if bare else ""
    return bare, ("" if whole == bare else whole)


def _topic_titles(
    tmdb: TMDBClient, topic: str, genres_by_type: dict[str, dict[str, int]], region: str,
    endorsed_items: list[dict] | None = None,
) -> tuple[list[Movie], bool, bool]:
    """Titles from or about `topic`, best-regarded first. A franchise or character is
    found by name ("Batman", "Harry Potter"); a subject by TMDB's keyword tags ("time
    travel", "heist"). `endorsed_items` are titles the model named for the topic (already
    looked up); they join the same ranking. Also returns whether there were enough solid
    matches to be sure the topic was understood, and whether it was matched by name."""
    wanted = _name_tokens(topic)
    squashed = "".join(wanted)

    def votes(item: dict) -> int:
        return int(item.get("vote_count") or 0)

    found: dict[tuple[str, int], dict] = {}
    named: set[tuple[str, int]] = set()
    for item in tmdb.search_multi_results(topic, page=1, limit=40, pages=2):
        tokens = _name_tokens(item.get("title") or item.get("name") or "")
        by_name = set(wanted) <= set(tokens) or (len(squashed) >= 6 and squashed in "".join(tokens))
        if by_name and votes(item) >= 30:
            key = (item["media_type"], int(item["id"]))
            found[key] = item
            named.add(key)
    solid_names = sum(1 for key in named if votes(found[key]) >= 100)

    lowered = topic.lower().strip()
    # TMDB tags are singular: "zombies" is filed under "zombie", "spies" under "spy".
    forms = [lowered]
    if lowered.endswith("s") and not lowered.endswith("ss"):
        forms.append(lowered[:-1])
        if lowered.endswith("ies"):
            forms.append(lowered[:-3] + "y")
    keywords = [k for form in forms for k in tmdb.search_keywords(form)]
    exact_ids = list(dict.fromkeys(k["id"] for k in keywords if k["name"].lower() in forms))
    # Longer tags that start with the topic ("marvel cinematic universe (mcu)") stand in
    # only when the exact tag is missing or nearly unused — otherwise "james bond" would
    # also bring in "james bond spoof".
    longer_ids = [k["id"] for k in keywords if k["name"].lower().startswith(lowered + " ")][:2]
    tagged: set[tuple[str, int]] = set()
    for keyword_ids in (exact_ids, longer_ids):
        if not keyword_ids or len(tagged) >= 15:
            continue
        for content_type in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
            for item in tmdb.discover_content(
                content_type, None, None, None, region=region, pages=2,
                keyword_ids=keyword_ids[:3], sort_by="vote_count.desc",     # the best-known, not this week's
            ):
                if votes(item) >= 50:
                    key = (content_type, int(item["id"]))
                    tagged.add(key)
                    found.setdefault(key, {**item, "media_type": content_type})
    # A well-used tag means a subject ("dogs", "heist"): what the titles are about counts
    # there, not a word in their name — "Reservoir Dogs" is not a film about dogs.
    is_subject = len(tagged) >= 15
    if is_subject:
        found = {key: item for key, item in found.items() if key in tagged}
    endorsed: set[tuple[str, int]] = set()
    for item in endorsed_items or []:
        key = (item["media_type"], int(item["id"]))
        found.setdefault(key, item)
        endorsed.add(key)

    if solid_names >= 3 and not is_subject:
        # A franchise: its best-known films also bring in the rest of their series, which
        # may not carry the name — "The Dark Knight" for Batman, "The Empire Strikes
        # Back" for Star Wars.
        leading = sorted(
            (key for key in named if key[0] == CONTENT_TYPE_MOVIE), key=lambda key: votes(found[key]), reverse=True
        )[:5]
        details = tmdb.map_parallel(lambda key: tmdb.get_content_details(key[1], key[0], region=region), leading)
        # The best-known film's own series, and any series named after the topic. Other
        # films' series stray: "Batman v Superman" would bring in "Man of Steel".
        collection_ids = list(dict.fromkeys(
            d.info["collection_id"] for position, d in enumerate(details)
            if d is not None and d.info.get("collection_id")
            and (position == 0 or set(wanted) <= set(_name_tokens(d.info.get("collection_name") or "")))
        ))[:3]
        for parts in tmdb.map_parallel(tmdb.get_collection_parts, collection_ids):
            for item in parts or []:
                key = (CONTENT_TYPE_MOVIE, int(item["id"]))
                if votes(item) >= 100:
                    found.setdefault(key, {**item, "media_type": CONTENT_TYPE_MOVIE})
                    named.add(key)

    def regard(key: tuple[str, int]) -> float:
        item = found[key]
        score = float(item.get("vote_average") or 0) * math.log10(1 + votes(item))
        # What marks a title as belonging: for a franchise its name (or series), for a
        # subject the model having picked it out.
        return score * (1.25 if key in (endorsed if is_subject else named) else 1.0)

    lookups = {
        ctype: {gid: gname for gname, gid in genres_by_type.get(ctype, {}).items()}
        for ctype in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)
    }
    titles = [
        build_movie_from_tmdb(found[key], lookups[key[0]], None, key[0])
        for key in sorted(found, key=regard, reverse=True)
    ]
    return titles, is_subject or solid_names >= 3, solid_names >= 3 and not is_subject


def _resolve_subject(
    tmdb: TMDBClient, subject: str, genres_by_type: dict[str, dict[str, int]], region: str,
    allow_title: bool = True,
) -> tuple[str, str] | None:
    """What the leftover words of a request name on TMDB: ("topic", subject) for a
    franchise, character or subject with plenty of titles; ("person", full name) for an
    actor or director; ("title", title) for one specific title. None when TMDB doesn't
    back any of them up, so a stray word never turns into a wrong search.
    `allow_title` is off when the request also carries filters ("feel-good family
    films"): the leftover is then more likely a description than a title."""
    titles, confident, _ = _topic_titles(tmdb, subject, genres_by_type, region)
    if confident:
        return "topic", subject
    wanted = set(_name_tokens(subject))
    for person in tmdb.search_person(subject, limit=5):
        if wanted <= set(_name_tokens(person.get("name") or "")) and person.get("known_for") and float(person.get("popularity") or 0) >= 1:
            return "person", person["name"]
    for movie in titles if allow_title else []:
        if _name_tokens(movie.title) == _name_tokens(subject):
            return "title", movie.title
    return None


def _split_year(title: str) -> tuple[str, int | None]:
    """"Batman (1989)" → ("Batman", 1989). The model often adds the year, and TMDB's
    search finds nothing for a title with one attached."""
    match = re.search(r"\s*\((\d{4})\)\s*$", title)
    return (title[:match.start()].strip(), int(match.group(1))) if match else (title.strip(), None)


def _titles_on_tmdb(tmdb: TMDBClient, names: list[str], content_type: str | None = None) -> list[dict]:
    """Look up titles the model named so that only real ones become cards. Returns TMDB's
    own entries (with "media_type"), in the model's order; a year the model gave picks
    between films of the same name."""
    wanted = [_split_year(name) for name in names]
    items: list[dict] = []
    for (title, year), results in zip(
        wanted, tmdb.map_parallel(lambda pair: tmdb.search_multi_results(pair[0], page=1, limit=6), wanted)
    ):
        usable = [
            r for r in (results or [])
            if (not content_type or r.get("media_type") == content_type) and int(r.get("vote_count") or 0) >= 30
        ]
        exact = [r for r in usable if (r.get("title") or r.get("name") or "").lower() == title.lower()]
        dated = [r for r in exact if year and (r.get("release_date") or r.get("first_air_date") or "").startswith(str(year))]
        if dated or exact or usable:
            items.append((dated or exact or usable)[0])
    return items


def _on_platforms(tmdb: TMDBClient, region: str, movies: list[Movie], platforms: list[str], limit: int = 18) -> list[Movie]:
    """Keep the titles that stream on any of `platforms` in the region. Lists that don't
    come from a platform-filtered query are checked title by title."""
    if not platforms or not movies:
        return movies
    movies = movies[:limit]
    details = tmdb.map_parallel(
        lambda m: tmdb.get_content_details(m.tmdb_id, m.content_type, region=region), movies
    )
    return [m for m, d in zip(movies, details) if d is not None and set(d.providers) & set(platforms)]


def _era_phrase(year_from: int | None, year_to: int | None) -> str:
    if year_from and year_to:
        return f" from {year_from}" if year_from == year_to else f" from {year_from}–{year_to}"
    if year_from:
        return f" from {year_from} onwards"
    return f" from {year_to} or earlier" if year_to else ""


def _search_lead_in(
    *, genres: list[str], languages: list[str], platforms: list[str], content_type: str,
    year_from: int | None, year_to: int | None, personalised: bool,
) -> str:
    """The sentence above the cards when the model hasn't written one. It says what was
    searched for ("Here are some Telugu thriller movies on Netflix.") so the reply
    reflects the request instead of a one-size-fits-all line."""
    noun = "movies" if content_type == CONTENT_TYPE_MOVIE else "shows" if content_type == CONTENT_TYPE_TV else "titles"
    described = " ".join([" and ".join(languages[:2])] * bool(languages) + [g.lower() for g in genres[:2]])
    where = f" on {' or '.join(platforms[:2])}" if platforms else ""
    when = _era_phrase(year_from, year_to)
    if not (described or where or when):
        if content_type:
            return f"Here are some {noun} I think you'll like." if personalised else f"Here are some popular {noun}."
        return "Here are some picks based on what you've watched." if personalised else "Here are some popular picks to get you started."
    return f"Here are some {described + ' ' if described else ''}{noun}{where}{when}."


_EMPTY_FILTERS = {"genres": [], "platforms": [], "languages": [], "content_type": ""}


def _chat_response(db: Database, user_id: int, reply: str, recommendations: list | None = None,
                   error: str | None = None, filters: dict | None = None, data_changed: bool = False):
    """Save the assistant turn (with its cards, so history can restore them) and respond."""
    recommendations = (recommendations or [])[:CHAT_CARDS_SHOWN]
    db.save_chat_message(
        user_id, "assistant", reply,
        recs_json=json.dumps(recommendations) if recommendations else None,
    )
    return jsonify({
        "ok": True,
        "reply": reply,
        "recommendations": recommendations,
        "error": error,
        "filters_used": filters or dict(_EMPTY_FILTERS),
        # True when the chat changed the user's library, so the client can drop cached lists.
        "data_changed": data_changed,
    })


@app.route("/api/chat", methods=["POST", "OPTIONS"])
@api_login_required
def api_chat() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        payload = request.get_json(silent=True) or {}
        user_message = (payload.get("message") or "").strip()[:600]
        if not user_message:
            return jsonify({"ok": False, "error": "Empty message."}), 400

        db = get_db()
        user_id = int(session["user_id"])

        # ── Context from the database (fast — no network) ─────────────────────
        watched_from_db = db.list_watched_items(user_id)
        watchlist_from_db = db.list_watchlist_items(user_id)
        taste_profile = _build_taste_profile(watched_from_db, watchlist_from_db)
        history = db.get_chat_history(user_id, limit=12)
        db.save_chat_message(user_id, "user", user_message)

        # ── 1. Small talk: answer straight away ───────────────────────────────
        if _is_obvious_greeting(user_message):
            return _chat_response(db, user_id, _call_chat_smalltalk(user_message, history, taste_profile))

        # ── 2. What the app can search on, for this user's region ─────────────
        tmdb = get_tmdb()
        region = current_region()
        genres_by_type, providers_by_type, languages, _ = fetch_dashboard_metadata(tmdb, region)
        genre_options = sorted({name for genres in genres_by_type.values() for name in genres.keys()})
        provider_options = sorted({p.provider_name for providers in providers_by_type.values() for p in providers})
        language_options = sorted(languages.keys())
        genre_set = set(genre_options)

        kw = _extract_filters_keyword(user_message, genre_options, provider_options, language_options)

        # ── 3. Understand the message: the model first, keywords as the fallback ──
        agent = _call_chat_agent(
            user_message, history, genre_options,
            [p.provider_name for p in top_providers(providers_by_type, region, limit=20)],
            language_options, taste_profile, SUPPORTED_REGIONS[region],
        )
        def _named_request() -> dict | None:
            """What the words left over after the filters name on TMDB — "get me batman
            movies" → the topic Batman; "mahesh babu films" → the person; "jawan" → the title."""
            bare, whole = _extract_subject(user_message, genre_options, provider_options, language_options)
            has_filters = bool(kw["genres"] or kw["languages"] or kw["platforms"])
            resolved = None
            try:
                # The name with its filter-like words first: "star wars" before "star".
                for subject, allow_title in ((whole, True), (bare, not has_filters)):
                    resolved = _resolve_subject(tmdb, subject, genres_by_type, region, allow_title) if subject else None
                    if resolved:
                        break
            except RuntimeError:
                return None
            if not resolved:
                return None
            kind, name = resolved
            if kind == "topic":
                # As the user wrote it, small words included: "war of the worlds", not "war worlds".
                typed = re.search(
                    r"\b" + r"\b[\w\s'&:.\-]*?\b".join(re.escape(word) for word in name.split()) + r"\b",
                    user_message, re.IGNORECASE,
                )
                if typed and len(typed.group().split()) <= len(name.split()) + 3:
                    name = typed.group()
            return {
                "intent": "movie_info" if kind == "title" else "filter",
                "reference_title": name if kind == "title" else "",
                "person": name if kind == "person" else "",
                "topic": name if kind == "topic" else "",
            }

        topic_name = ""
        if agent:
            intent = agent["intent"]
            reference_title = agent["reference_title"]
            person_name = agent["person"]
            topic_name = agent["topic"]
            nothing_recognised = not (
                person_name or topic_name or agent["genres"] or agent["languages"] or agent["platforms"]
                or kw["genres"] or kw["languages"] or kw["platforms"]
            )
            if intent == "filter" and nothing_recognised:
                # The model found no filter at all, yet the request may name something.
                named = _named_request()
                if named:
                    intent, reference_title = named["intent"], named["reference_title"]
                    person_name, topic_name = named["person"], named["topic"]
        else:
            # The model is unreachable: work from keywords alone.
            person_name = ""
            agent = {"genres": [], "exclude_genres": [], "platforms": [], "languages": [], "content_type": "",
                     "year_from": None, "year_to": None, "runtime_max": None, "watchlist_op": "",
                     "similar_titles": [], "reply": "", "topic": ""}
            command = _keyword_library_command(user_message, history)
            pointed_card = _card_at_position(user_message, history)
            if command:
                intent, reference_title = "watchlist_action", command[1]
                agent["watchlist_op"] = command[0]
            elif pointed_card:
                # "tell me about the second one" — answered from TMDB's facts.
                intent, reference_title = "movie_info", pointed_card["title"]
            elif re.search(r"\bmy\s+(?:taste|watch(?:ed)?\s+history|viewing\s+history|history)\b", user_message.lower()):
                intent, reference_title = "user_stats", ""
            else:
                reference_title = _extract_reference_title(user_message) or ""
                named = None if reference_title else _named_request()
                lowered = user_message.lower()
                asks_question = "?" in lowered or re.match(r"\s*(?:who|what|why|how|when|where|which|is|are|do|does|did|can|could|tell)\b", lowered)
                # "is Jawan on Netflix?" asks for a fact: the platform in it is not a filter.
                asks_fact = re.match(r"\s*(?:who|why|when|where|how(?!\s+about)|is|are|was|were|do|does|did|has|have)\b", lowered)
                wants_picks = re.search(r"\b(?:watch|recommend|suggest|pick|find|show me)\b", lowered)
                extracted = kw["genres"] or kw["exclude_genres"] or reference_title or (
                    not asks_fact and (kw["platforms"] or kw["languages"])
                )
                if named:
                    intent, reference_title = named["intent"], named["reference_title"]
                    person_name, topic_name = named["person"], named["topic"]
                else:
                    if asks_question and not wants_picks and not extracted:
                        # Random picks would be a non-answer to a question.
                        return _chat_response(db, user_id, (
                            "I can't answer questions just now, but I can still find you something to watch — "
                            "try “Telugu thriller on Netflix” or “movies like Inception”."
                        ))
                    if _is_general_message(user_message, kw["genres"], kw["platforms"], kw["languages"]):
                        return _chat_response(db, user_id, _build_general_reply(user_message, taste_profile))
                    intent = "similarity" if reference_title else "filter"

        # A filter word inside the name that was asked for is part of the name, not a
        # filter: "Star Wars" is not a request for the War genre.
        named_text = topic_name or person_name or reference_title
        if named_text:
            if re.search(re.escape(named_text), user_message, re.IGNORECASE):
                without_name = re.sub(re.escape(named_text), " ", user_message, flags=re.IGNORECASE)
            else:       # the model's spelling of it ("World War II" for "world war 2")
                without_name = re.sub(
                    r"\b(?:" + "|".join(re.escape(word) for word in _name_tokens(named_text)) + r")\b",
                    " ", user_message, flags=re.IGNORECASE,
                )
            kw = _extract_filters_keyword(without_name, genre_options, provider_options, language_options)

        # "the second one": the exact card, whatever the model counted.
        pointed = _card_at_position(user_message, history)
        if pointed and intent in ("movie_info", "watchlist_action", "similarity"):
            reference_title = pointed["title"]

        # ── 4. Intents that don't need a search ───────────────────────────────
        if intent == "general":
            return _chat_response(db, user_id, agent["reply"] or _build_general_reply(user_message, taste_profile))

        if intent == "user_stats":
            reply = agent["reply"]
            if not reply:
                parts = []
                if taste_profile.get("top_genres"):
                    parts.append(f"You lean towards {', '.join(taste_profile['top_genres'][:3])}")
                if taste_profile.get("recent_watched"):
                    parts.append(f"and most recently watched {taste_profile['recent_watched'][0]}")
                reply = (" ".join(parts) + ".") if parts else "Mark a few titles as watched and I'll be able to tell you about your taste."
            return _chat_response(db, user_id, reply)

        if intent == "movie_info":
            if not reference_title:
                return _chat_response(db, user_id, agent["reply"] or "Which title would you like to know about?")
            try:
                reply, cards = _answer_about_title(tmdb, region, reference_title, user_message, _shown_cards(history))
            except RuntimeError:
                reply, cards = _TMDB_DOWN_REPLY, []
            if not cards and tmdb.unreachable:
                reply = _TMDB_DOWN_REPLY
            return _chat_response(db, user_id, reply, cards)

        if intent == "watchlist_action":
            try:
                reply, changed = _apply_library_action(
                    db, tmdb, user_id, region, reference_title, agent["watchlist_op"] or "add",
                    _shown_cards(history),
                )
            except RuntimeError:
                reply, changed = _TMDB_DOWN_REPLY, False
            if not changed and tmdb.unreachable:
                reply = _TMDB_DOWN_REPLY
            return _chat_response(db, user_id, reply, data_changed=changed)

        # ── 5. Search criteria: the model's value where it gave one, keywords otherwise ──
        # Nobody asks for more than three genres at once: a longer list is the model copying
        # the taste profile, so the genres actually named in the message are used instead
        # (discovery leans on the profile by itself when there are none).
        model_genres = agent["genres"] if len(agent["genres"]) <= 3 else []
        genres_filter = _canonical_names(model_genres, genre_options, _GENRE_ALIASES) or [g for g in kw["genres"] if g in genre_set]
        language_names = _canonical_names(agent["languages"], language_options, _LANGUAGE_ALIASES) or kw["languages"]
        exclude_genres_filter = list(dict.fromkeys(
            _canonical_names(agent["exclude_genres"], genre_options, _GENRE_ALIASES) + kw["exclude_genres"]
        ))
        # A genre can't be both wanted and avoided — the exclusion wins.
        genres_filter = [g for g in genres_filter if g not in exclude_genres_filter]

        platforms_filter: list[str] = []
        for name in agent["platforms"] or kw["platforms"]:
            matched = _fuzzy_match_provider(name, provider_options)
            if matched and matched not in platforms_filter:
                platforms_filter.append(matched)
        platforms_filter = platforms_filter[:3]

        content_type_raw = agent["content_type"] or kw["content_type"]
        # A length limit only makes sense for films: with a series it is dropped,
        # with no format named it means films.
        runtime_max = agent["runtime_max"] or _extract_runtime_max(user_message)
        if content_type_raw == CONTENT_TYPE_TV:
            runtime_max = None
        elif runtime_max:
            content_type_raw = CONTENT_TYPE_MOVIE
        content_types = [content_type_raw] if content_type_raw else None
        year_from, year_to = agent["year_from"], agent["year_to"]
        if not (year_from or year_to):
            year_from, year_to = _extract_year_range(user_message)
        year_range = (year_from, year_to) if (year_from or year_to) else None

        language_codes = [languages[name] for name in language_names if name in languages]
        rec_language = language_codes[0] if len(language_codes) == 1 else None
        preference_history = watched_from_db + watchlist_from_db
        watched_keys = {(m.content_type, m.tmdb_id) for m in watched_from_db}
        wanted_genres = _with_tv_genres(genres_filter)
        exclude_set = _with_tv_genres(exclude_genres_filter)

        lead_in = agent["reply"]
        notes: list[str] = []
        recommendations: list[dict] = []
        error_msg: str | None = None
        # What the default lead-in describes; a search that had to be widened updates these.
        described_genres, described_platforms = genres_filter, platforms_filter
        filters_used = {
            "genres": genres_filter,
            "exclude_genres": exclude_genres_filter,
            "platforms": platforms_filter,
            "languages": language_names,
            "content_type": content_type_raw,
            "person": person_name,
            "topic": topic_name,
            "year_from": year_from,
            "year_to": year_to,
            "runtime_max": runtime_max,
        }

        try:
            person = _find_person(tmdb, person_name) if person_name else None
            if person_name and (
                person is None
                # A one-word or little-known match may really be a character or franchise
                # ("Batman", "Marvel", "Harry Potter"): it is, if TMDB has plenty under that name.
                or ((len(_name_tokens(person_name)) == 1 or float(person.get("popularity") or 0) < 1)
                    and _topic_titles(tmdb, person_name, genres_by_type, region)[1])
            ):
                person, topic_name = None, topic_name or person_name
                filters_used["person"], filters_used["topic"] = "", topic_name
            found_name, person_works = _person_titles(tmdb, person, genres_by_type) if person else ("", [])

            if found_name:
                # ── Path P: films/shows with (or by) a person ─────────────────
                filters_used["person"] = found_name
                picks = [
                    m for m in person_works
                    if _keep(m, content_types=content_types, genres=wanted_genres, exclude=exclude_set,
                             language_codes=language_codes, year_from=year_from, year_to=year_to,
                             skip_keys=watched_keys)
                ]
                picks = _within_runtime(tmdb, region, picks, runtime_max)
                recommendations = [_card(m) for m in picks[:12]]
                lead_in = lead_in or f"Here's some of {found_name}'s best-known work."
                if not recommendations:
                    error_msg = (
                        f"I found {found_name}, but nothing of theirs matches those filters"
                        + (" that you haven't already watched." if person_works else ".")
                    )

            elif topic_name and not (intent == "similarity" and reference_title):
                # ── Path T: a franchise, character or subject ("Batman movies") ──
                suggested = _titles_on_tmdb(tmdb, agent["similar_titles"], content_type_raw or None)
                matches, _, by_name = _topic_titles(tmdb, topic_name, genres_by_type, region, suggested)
                picks = [
                    m for m in matches
                    if _keep(m, content_types=content_types, genres=wanted_genres, exclude=exclude_set,
                             language_codes=language_codes, year_from=year_from, year_to=year_to,
                             skip_keys=watched_keys)
                ]
                picks = _within_runtime(tmdb, region, picks, runtime_max)
                where = ""
                if platforms_filter and picks:
                    available = _on_platforms(tmdb, region, picks, platforms_filter)
                    if available:
                        picks, where = available, f" on {' or '.join(platforms_filter)}"
                    else:
                        notes.append(f"not limited to {', '.join(platforms_filter)} — nothing matched there")
                recommendations = [_card(m) for m in picks[:12]]
                noun = "movies" if content_type_raw == CONTENT_TYPE_MOVIE else "shows" if content_type_raw == CONTENT_TYPE_TV else "titles"
                label = topic_name if topic_name != topic_name.lower() else topic_name.title()
                when = _era_phrase(year_from, year_to)
                if by_name or suggested:
                    default_lead_in = f"Here are the {label} {noun}{when} I found{where}."
                elif topic_name.lower().endswith("s"):
                    default_lead_in = f"Here are some {noun} about {topic_name}{when}{where}."
                else:
                    default_lead_in = f"Here are some {topic_name} {noun}{when}{where}."
                lead_in = lead_in or default_lead_in
                if exclude_genres_filter and recommendations:
                    notes.append(f"skipping {', '.join(exclude_genres_filter).lower()}")
                if not recommendations:
                    error_msg = (
                        f"I found titles for “{topic_name}”, but none that fit the rest of what you asked for."
                        if (suggested or matches)
                        else f"I couldn't find anything for “{topic_name}” — could you check the spelling?"
                    )

            elif intent == "similarity" and reference_title:
                # ── Path A: titles like a reference title ─────────────────────
                ref_item = _find_title(tmdb, reference_title)
                if not ref_item:
                    error_msg = f"I couldn't find “{reference_title}” on TMDB — could you check the spelling?"
                else:
                    ref_ct = ref_item.get("media_type", CONTENT_TYPE_MOVIE)
                    ref_id = int(ref_item.get("id", 0))
                    ref_lookup = {gid: gname for gname, gid in genres_by_type.get(ref_ct, {}).items()}
                    ref_movie = build_movie_from_tmdb(ref_item, ref_lookup, None, ref_ct)
                    target_ct = content_type_raw or ref_ct
                    skip_keys = watched_keys | {(ref_ct, ref_id)}
                    similarity_history = [ref_movie] + preference_history
                    lead_in = lead_in or f"If you liked {ref_movie.title}, these should be right up your street."
                    picks: list[Recommendation] = []

                    skip_all = skip_keys | {(m.content_type, m.tmdb_id) for m in watchlist_from_db}
                    target_lookup = {gid: gname for gname, gid in genres_by_type.get(target_ct, {}).items()}

                    def _similar_ok(m: Movie) -> bool:
                        return _keep(m, content_types=[target_ct], genres=set(), exclude=exclude_set,
                                     language_codes=language_codes, year_from=year_from, year_to=year_to,
                                     skip_keys=skip_all)

                    # 1. The model's own suggestions — each one looked up on TMDB, so only
                    #    real titles become cards. With a platform filter, availability in the
                    #    user's region is checked per title.
                    suggested = [_split_year(t)[0] for t in agent["similar_titles"]]
                    suggested = [t for t in suggested if t.lower() != ref_movie.title.lower()]
                    lookups = tmdb.map_parallel(lambda t: tmdb.search_multi_results(t, page=1, limit=4), suggested)
                    candidates: list[Movie] = []
                    for suggestion, results in zip(suggested, lookups):
                        of_type = [r for r in (results or []) if r.get("media_type") == target_ct and int(r.get("vote_count") or 0) >= 30]
                        exact = [r for r in of_type if (r.get("title") or r.get("name") or "").lower() == suggestion.lower()]
                        if not (exact or of_type):
                            continue
                        m = build_movie_from_tmdb((exact or of_type)[0], target_lookup, None, target_ct)
                        if _similar_ok(m):
                            skip_all.add((m.content_type, m.tmdb_id))
                            candidates.append(m)
                    if platforms_filter and candidates:
                        details = tmdb.map_parallel(
                            lambda m: tmdb.get_content_details(m.tmdb_id, m.content_type, region=region), candidates
                        )
                        candidates = [
                            m for m, d in zip(candidates, details)
                            if d is not None and set(d.providers) & set(platforms_filter)
                        ]
                    candidates = _within_runtime(tmdb, region, candidates, runtime_max)
                    picks = [Recommendation(movie=m, score=0.0, reasons=[f"Similar in spirit to {ref_movie.title}"]) for m in candidates]

                    # 2. TMDB's "viewers also liked" list for the same kind of title, best
                    #    regarded first. (It can't be filtered by platform.)
                    if target_ct == ref_ct and not platforms_filter and len(picks) < 12:
                        also_liked = [
                            item for item in tmdb.get_similar_content(ref_id, ref_ct, pages=2, recommendations_first=True)
                            if int(item.get("vote_count") or 0) >= 200
                        ]
                        also_liked.sort(
                            key=lambda item: float(item.get("vote_average") or 0) * math.log10(1 + int(item.get("vote_count") or 0)),
                            reverse=True,
                        )
                        liked = [build_movie_from_tmdb(item, ref_lookup, None, ref_ct) for item in also_liked]
                        for m in _within_runtime(tmdb, region, [m for m in liked if _similar_ok(m)], runtime_max):
                            skip_all.add((m.content_type, m.tmdb_id))
                            picks.append(Recommendation(movie=m, score=0.0, reasons=[f"Viewers of {ref_movie.title} also liked this"]))

                    # Not enough (or a platform / other format was asked for): discover by
                    # the reference title's genres, its own language first.
                    if len(picks) < 6 and ref_movie.genres:
                        seen = set(skip_all)
                        fallback: list[Movie] = []
                        ref_languages = language_codes or ([ref_movie.language, None] if ref_movie.language else [None])
                        for lang in ref_languages:
                            for platform in (platforms_filter or [None]):
                                for movie in discover_catalog(
                                    tmdb=tmdb, genre_names=ref_movie.genres[:3], provider_name=platform,
                                    language_code=lang, genres_by_type=genres_by_type,
                                    providers_by_type=providers_by_type, content_types=[target_ct],
                                    region=region, year_range=year_range, runtime_max=runtime_max,
                                ):
                                    key = (movie.content_type, movie.tmdb_id)
                                    if key not in seen and not (exclude_set & set(movie.genres)):
                                        seen.add(key)
                                        fallback.append(movie)
                            if len(fallback) >= 12:
                                break
                        if fallback:
                            picks += _recs_within_runtime(tmdb, region, MovieRecommender(fallback).recommend(
                                genres=list(_with_tv_genres(ref_movie.genres)), platform=None, language=None,
                                watched_history=similarity_history, top_k=None,
                            ), runtime_max)

                    recommendations = [serialize_recommendation(r) for r in picks[:12]]
                    if exclude_genres_filter and recommendations:
                        notes.append(f"skipping {', '.join(exclude_genres_filter).lower()}")
                    if not recommendations:
                        error_msg = f"I couldn't find titles similar to {ref_movie.title} with those filters — try loosening them."

            else:
                # ── Path B: discovery by criteria, relaxing step by step ──────
                discovery_genres = genres_filter or None
                if not discovery_genres and taste_profile["top_genres"]:
                    discovery_genres = [g for g in taste_profile["top_genres"] if g not in exclude_genres_filter][:3]

                # No language named → no language restriction; ranking already leans
                # towards the languages in the user's history.
                discovery_lang_codes = language_codes

                sibling_genres: list[str] = list(genres_filter)
                for g in genres_filter:
                    for sg in _GENRE_SIBLINGS.get(g, []):
                        if sg not in sibling_genres and sg not in exclude_genres_filter and sg in genre_set:
                            sibling_genres.append(sg)

                # TMDB returns each query most-popular-first; remember every title's best
                # position so ranking can prefer well-known titles when taste scores are close.
                popularity_rank: dict[tuple[str, int], int] = {}

                def _build_catalog(genre_names, plat_list, lang_codes):
                    seen_: set = set()
                    cat_: list[Movie] = []
                    for lang in (lang_codes or [None]):
                        for plat in (plat_list or [None]):
                            position: dict[str, int] = {}
                            for mv in discover_catalog(
                                tmdb=tmdb, genre_names=genre_names, provider_name=plat,
                                language_code=lang, genres_by_type=genres_by_type,
                                providers_by_type=providers_by_type, content_types=content_types,
                                region=region, year_range=year_range, runtime_max=runtime_max,
                            ):
                                k = (mv.content_type, mv.tmdb_id)
                                rank = position.get(mv.content_type, 0)
                                position[mv.content_type] = rank + 1
                                popularity_rank[k] = min(popularity_rank.get(k, rank), rank)
                                if k not in seen_:
                                    seen_.add(k)
                                    cat_.append(mv)
                    return cat_

                def _popularity_bonus(rec: Recommendation) -> float:
                    rank = popularity_rank.get((rec.movie.content_type, rec.movie.tmdb_id), 99)
                    return 3.0 * max(0.0, 1.0 - rank / 60)

                def _score_and_filter(catalog, required_genres, required_language):
                    if not catalog:
                        return []
                    recs_ = MovieRecommender(catalog).recommend(
                        genres=list(wanted_genres) or None, platform=None,
                        language=rec_language, watched_history=preference_history, top_k=None,
                    )
                    recs_ = filter_recommendations_against_watched(recs_, watched_from_db)
                    recs_.sort(key=lambda r: r.score + _popularity_bonus(r), reverse=True)
                    if required_genres:
                        recs_ = [r for r in recs_ if required_genres & set(r.movie.genres)]
                    if required_language:
                        recs_ = [r for r in recs_ if r.movie.language == required_language]
                    if exclude_set:
                        recs_ = [r for r in recs_ if not (exclude_set & set(r.movie.genres))]
                    return recs_

                recs = _score_and_filter(
                    _build_catalog(discovery_genres, platforms_filter, discovery_lang_codes),
                    wanted_genres, rec_language,
                )

                if len(recs) < 3 and sibling_genres != genres_filter:
                    recs2 = _score_and_filter(
                        _build_catalog(sibling_genres, platforms_filter, discovery_lang_codes),
                        _with_tv_genres(sibling_genres), rec_language,
                    )
                    if len(recs2) > len(recs):
                        recs = recs2
                        notes.append("including closely related genres")

                if len(recs) < 3 and platforms_filter:
                    recs3 = _score_and_filter(
                        _build_catalog(sibling_genres or discovery_genres, [], discovery_lang_codes),
                        _with_tv_genres(sibling_genres), rec_language,
                    )
                    if len(recs3) > len(recs):
                        recs = recs3
                        described_platforms = []
                        notes.append(f"not limited to {', '.join(platforms_filter)} — nothing matched there")

                if len(recs) < 3 and genres_filter:
                    recs4 = _score_and_filter(_build_catalog(None, [], discovery_lang_codes), set(), rec_language)
                    if len(recs4) > len(recs):
                        recs = recs4
                        described_genres, described_platforms = [], []
                        notes.append("a broader mix — that exact genre came up short")

                recs = _recs_within_runtime(tmdb, region, recs, runtime_max)
                recommendations = [serialize_recommendation(r) for r in recs[:12]]
                if not recommendations:
                    error_msg = "Nothing matched that combination — try a different genre, language or platform."

        except RuntimeError:
            recommendations = []
            error_msg = "The movie database returned an error — please try again in a moment."

        # ── 6. Reply: never promise results that aren't there ─────────────────
        if not recommendations and tmdb.unreachable:
            return _chat_response(db, user_id, _TMDB_DOWN_REPLY, filters=filters_used)
        if recommendations:
            bot_reply = lead_in or _search_lead_in(
                genres=described_genres, languages=language_names, platforms=described_platforms,
                content_type=content_type_raw, year_from=year_from, year_to=year_to,
                personalised=bool(watched_from_db),
            )
            if runtime_max:
                notes.append(f"all {runtime_max} minutes or shorter")
            if notes:
                bot_reply = bot_reply.rstrip(" .!:;,") + f" ({'; '.join(notes)})."
        else:
            bot_reply = "I couldn't find a good match for that one."

        return _chat_response(db, user_id, bot_reply, recommendations, error_msg, filters_used)

    except Exception:
        import traceback
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": "Something went wrong on the server.",   # details go to the server log only
            "reply": "Sorry, I ran into an error. Please try again.",
            "recommendations": [],
            "filters_used": {},
        }), 500


@app.route("/api/chat/history", methods=["GET", "OPTIONS"])
@api_login_required
def api_chat_history() -> Any:
    if request.method == "OPTIONS":
        return ("", 204)
    import json as _json
    user_id = int(session["user_id"])
    raw_history = get_db().get_chat_history(user_id, limit=60)
    history = []
    for m in raw_history:
        entry = {"role": m["role"], "content": m["content"]}
        if m.get("recs_json"):
            try:
                entry["recs"] = _json.loads(m["recs_json"])
            except Exception:
                entry["recs"] = []
        history.append(entry)
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


def _prewarm_caches() -> None:
    """Fill TMDB caches in the background so the first dashboard load isn't cold."""
    try:
        tmdb = get_tmdb()
        fetch_dashboard_metadata(tmdb, REGION)
        tmdb.get_trending(limit=16)
        tmdb.get_ott_releases(region=REGION, days=60, provider_ids=ott_providers_for(tmdb, REGION)[0])
        _get_daily_pick_candidates(tmdb)
    except Exception:
        pass


def _start_prewarm() -> None:
    if os.getenv("TMDB_API_KEY", "").strip():
        threading.Thread(target=_prewarm_caches, daemon=True).start()


if __name__ == "__main__":
    # With the reloader on, only the serving child process should warm up.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _start_prewarm()
    app.run(debug=True, port=5001, threaded=True)
else:
    _start_prewarm()
