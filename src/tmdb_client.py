from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .recommender import Movie

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

CONTENT_TYPE_MOVIE = "movie"
CONTENT_TYPE_TV = "tv"
SUPPORTED_CONTENT_TYPES = {CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV}

MOOD_BY_GENRE = {
    "Action": ["Adrenaline", "Intense", "Energetic"],
    "Adventure": ["Uplifting", "Fun", "Energetic"],
    "Animation": ["Feel-good", "Warm", "Light"],
    "Comedy": ["Light", "Fun", "Feel-good"],
    "Crime": ["Dark", "Serious", "Intense"],
    "Documentary": ["Thoughtful", "Calm", "Curious"],
    "Drama": ["Emotional", "Thoughtful", "Serious"],
    "Family": ["Warm", "Feel-good", "Light"],
    "Fantasy": ["Dreamy", "Whimsical", "Uplifting"],
    "History": ["Thoughtful", "Serious", "Calm"],
    "Horror": ["Scary", "Dark", "Intense"],
    "Music": ["Energetic", "Feel-good", "Warm"],
    "Mystery": ["Suspenseful", "Dark", "Clever"],
    "Romance": ["Romantic", "Warm", "Bittersweet"],
    "Science Fiction": ["Mind-bending", "Thoughtful", "Atmospheric"],
    "TV Movie": ["Light", "Calm", "Feel-good"],
    "Thriller": ["Suspenseful", "Intense", "Dark"],
    "War": ["Serious", "Intense", "Thoughtful"],
    "Western": ["Gritty", "Serious", "Atmospheric"],
}


@dataclass(frozen=True)
class Provider:
    provider_id: int
    provider_name: str


@dataclass(frozen=True)
class ContentDetails:
    movie: Movie
    genres: list[str]
    cast: list[str]
    providers: list[str]
    original_rating: float


def _image_url(path: str | None, size: str) -> str:
    if not path:
        return ""
    return f"{TMDB_IMAGE_BASE_URL}/{size}{path}"


class TMDBClient:
    def __init__(
        self,
        api_key: str,
        cache_ttl_seconds: int = 600,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.75,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        self.api_key = api_key
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._cache: dict[str, tuple[float, dict | list]] = {}

    @classmethod
    def from_env(cls) -> "TMDBClient":
        api_key = os.getenv("TMDB_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "TMDB_API_KEY is not set. Create a TMDB account and export TMDB_API_KEY."
            )
        timeout_seconds = float(os.getenv("TMDB_TIMEOUT_SECONDS", "5").strip() or "5")
        max_retries = int(os.getenv("TMDB_MAX_RETRIES", "2").strip() or "2")
        retry_backoff_seconds = float(
            os.getenv("TMDB_RETRY_BACKOFF_SECONDS", "0.75").strip() or "0.75"
        )
        return cls(
            api_key=api_key,
            request_timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    def _assert_content_type(self, content_type: str) -> None:
        if content_type not in SUPPORTED_CONTENT_TYPES:
            raise ValueError(f"Unsupported content type: {content_type}")

    def _network_error_message(self, reason: object) -> str:
        reason_text = str(reason).strip() or "request timed out"
        return (
            "TMDB is currently unreachable from this network. "
            f"Connection detail: {reason_text}. "
            "If you are in India, try Cloudflare DNS (1.1.1.1), Google DNS (8.8.8.8), "
            "Secure DNS, or a VPN."
        )

    def _get(self, path: str, params: dict[str, str | int]) -> dict | list:
        query_params = {"api_key": self.api_key, **params}
        query = urllib.parse.urlencode(query_params)
        cache_key = f"{path}?{query}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self.cache_ttl_seconds:
            return cached[1]

        url = f"{TMDB_BASE_URL}{path}?{query}"
        request = urllib.request.Request(
            url, headers={"accept": "application/json", "User-Agent": "movie-bot/1.0"}
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.request_timeout_seconds
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self._cache[cache_key] = (now, payload)
                return payload
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < self.max_retries - 1:
                    retry_after = int(error.headers.get("Retry-After", "1"))
                    time.sleep(max(1, retry_after))
                    last_error = error
                    continue
                if error.code >= 500 and attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff_seconds * (2**attempt))
                    last_error = error
                    continue
                error_body = error.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"TMDB request failed ({error.code}): {error_body}") from error
            except (urllib.error.URLError, TimeoutError, ConnectionResetError) as error:
                last_error = error
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff_seconds * (2**attempt))
                    continue
                reason = getattr(error, "reason", str(error))
                raise RuntimeError(self._network_error_message(reason)) from error

        raise RuntimeError(self._network_error_message(last_error))

    def get_languages(self) -> dict[str, str]:
        payload = self._get("/configuration/languages", {})
        if not isinstance(payload, list):
            return {}

        languages: dict[str, str] = {}
        for item in payload:
            iso_639_1 = (item.get("iso_639_1") or "").strip()
            english_name = (item.get("english_name") or "").strip()
            if not iso_639_1 or not english_name:
                continue
            languages[english_name] = iso_639_1
        return dict(sorted(languages.items(), key=lambda pair: pair[0]))

    def get_genres(self, content_type: str) -> dict[str, int]:
        self._assert_content_type(content_type)
        payload = self._get(f"/genre/{content_type}/list", {"language": "en-US"})
        if not isinstance(payload, dict):
            return {}
        genres = payload.get("genres", [])
        return {item["name"]: item["id"] for item in genres}

    def get_providers(
        self, content_type: str, region: str = "US", pages: int = 3
    ) -> list[Provider]:
        self._assert_content_type(content_type)
        all_providers: dict[int, Provider] = {}
        for page in range(1, pages + 1):
            payload = self._get(
                f"/watch/providers/{content_type}",
                {"language": "en-US", "watch_region": region, "page": page},
            )
            if not isinstance(payload, dict):
                continue
            for item in payload.get("results", []):
                provider = Provider(
                    provider_id=item["provider_id"], provider_name=item["provider_name"]
                )
                all_providers[provider.provider_id] = provider
            total_pages = int(payload.get("total_pages", page))
            if page >= total_pages:
                break
        return sorted(all_providers.values(), key=lambda provider: provider.provider_name)

    def get_trending(self, limit: int = 10) -> list[dict]:
        payload = self._get("/trending/all/day", {"language": "en-US"})
        if not isinstance(payload, dict):
            return []
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []
        return results[:limit]

    def search_multi_results(
        self, title: str, page: int = 1, limit: int = 10, pages: int = 1
    ) -> list[dict]:
        """Search TMDB across `pages` pages and return deduplicated results up to `limit`."""
        all_items: dict[tuple, dict] = {}
        for p in range(page, page + pages):
            try:
                payload = self._get(
                    "/search/multi",
                    {"query": title, "language": "en-US", "include_adult": "false", "page": p},
                )
            except RuntimeError:
                break
            if not isinstance(payload, dict):
                break
            results = payload.get("results", [])
            if not isinstance(results, list):
                break
            for r in results:
                if r.get("media_type") in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
                    key = (r.get("media_type"), r.get("id"))
                    if key not in all_items:
                        all_items[key] = r
            total = int(payload.get("total_pages", p))
            if p >= total:
                break
        return list(all_items.values())[:limit]

    def search_content_results(
        self, title: str, content_type: str, page: int = 1, limit: int = 10
    ) -> list[dict]:
        self._assert_content_type(content_type)
        payload = self._get(
            f"/search/{content_type}",
            {
                "query": title,
                "language": "en-US",
                "include_adult": "false",
                "page": page,
            },
        )
        if not isinstance(payload, dict):
            return []
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []
        return results[:limit]

    def get_content_details(
        self, tmdb_id: int, content_type: str, region: str = "IN"
    ) -> ContentDetails:
        self._assert_content_type(content_type)
        payload = self._get(
            f"/{content_type}/{tmdb_id}",
            {"language": "en-US", "append_to_response": "credits,watch/providers"},
        )
        if not isinstance(payload, dict):
            raise RuntimeError("TMDB returned an unexpected detail payload.")

        genres = [genre.get("name", "") for genre in payload.get("genres", [])]
        genres = [genre for genre in genres if genre]

        cast_entries = payload.get("credits", {}).get("cast", [])
        cast = [entry.get("name", "") for entry in cast_entries[:10]]
        cast = [name for name in cast if name]

        provider_bucket = payload.get("watch/providers", {}).get("results", {}).get(region, {})
        provider_names: list[str] = []
        for key in ("flatrate", "free", "ads", "rent", "buy"):
            for provider in provider_bucket.get(key, []):
                provider_name = provider.get("provider_name", "")
                if provider_name and provider_name not in provider_names:
                    provider_names.append(provider_name)

        genre_lookup = {genre["id"]: genre["name"] for genre in payload.get("genres", []) if genre.get("id")}
        movie = build_movie_from_tmdb(
            raw_item=payload,
            genre_lookup=genre_lookup,
            selected_platform=None,
            content_type=content_type,
        )
        return ContentDetails(
            movie=movie,
            genres=genres,
            cast=cast,
            providers=provider_names,
            original_rating=float(payload.get("vote_average", 0.0)),
        )

    def get_ott_releases(self, region: str = "IN", days: int = 60) -> list[dict]:
        from datetime import datetime, timedelta

        today = datetime.now()
        date_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        # Netflix|Amazon Prime Video|Disney+ Hotstar|Zee5|SonyLIV|Aha|Sun NXT
        provider_ids = "8|119|122|232|237|532|309"

        # Query each language separately — TMDB only accepts one code per filter
        target_languages = ["te", "hi", "ta", "kn", "ml", "en"]

        all_items: dict[str, dict] = {}

        for content_type in [CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV]:
            date_gte = "primary_release_date.gte" if content_type == CONTENT_TYPE_MOVIE else "first_air_date.gte"
            date_lte = "primary_release_date.lte" if content_type == CONTENT_TYPE_MOVIE else "first_air_date.lte"
            sort_field = "primary_release_date.desc" if content_type == CONTENT_TYPE_MOVIE else "first_air_date.desc"

            for lang in target_languages:
                page = 1
                while True:
                    params: dict[str, str | int] = {
                        "language": "en-US",
                        "sort_by": sort_field,
                        "include_adult": "false",
                        "watch_region": region,
                        "with_watch_providers": provider_ids,
                        "with_watch_monetization_types": "flatrate",
                        "with_original_language": lang,
                        "page": page,
                        date_gte: date_from,
                        date_lte: date_to,
                    }
                    if content_type == CONTENT_TYPE_MOVIE:
                        params["include_video"] = "false"

                    try:
                        payload = self._get(f"/discover/{content_type}", params)
                        if not isinstance(payload, dict):
                            break
                        for item in payload.get("results", []):
                            key = f"{content_type}-{item['id']}"
                            item_copy = dict(item)
                            item_copy["_content_type"] = content_type
                            all_items[key] = item_copy
                        total_pages = int(payload.get("total_pages", 1))
                        if page >= total_pages or page >= 10:
                            break
                        page += 1
                    except RuntimeError:
                        break

        # Sort by language priority first, then by release date descending within each group.
        # Two stable passes: date desc first, then language priority asc (stable preserves date order).
        _lang_priority = {"te": 0, "hi": 1, "ta": 2, "kn": 3, "ml": 4, "en": 5}

        by_date = sorted(
            all_items.values(),
            key=lambda x: x.get("release_date") or x.get("first_air_date") or "",
            reverse=True,
        )
        sorted_items = sorted(
            by_date,
            key=lambda x: _lang_priority.get(x.get("original_language", ""), 99),
        )

        results: list[dict] = []
        for item in sorted_items:
            content_type = item["_content_type"]
            title = (item.get("title") or item.get("name") or "Unknown").strip()
            release_date = (item.get("release_date") or item.get("first_air_date") or "").strip()
            results.append({
                "tmdb_id": int(item.get("id", 0)),
                "title": title,
                "poster_url": _image_url(item.get("poster_path"), "w342"),
                "content_type": content_type,
                "year": release_date[:4] if release_date else "",
                "release_date": release_date,
            })

        return results

    def get_daily_pick_candidates(
        self,
        language: str,
        content_type: str,
        pages: int = 2,
    ) -> list[dict]:
        """Fetch highly-rated, popular content for the daily pick pool."""
        self._assert_content_type(content_type)
        results: dict[int, dict] = {}
        for page in range(1, pages + 1):
            try:
                payload = self._get(f"/discover/{content_type}", {
                    "language": "en-US",
                    "sort_by": "popularity.desc",
                    "vote_count.gte": 500,
                    "vote_average.gte": 7.0,
                    "with_original_language": language,
                    "include_adult": "false",
                    "page": page,
                })
                if isinstance(payload, dict):
                    for item in payload.get("results", []):
                        results[item["id"]] = item
            except RuntimeError:
                break
        return list(results.values())

    def get_similar_content(
        self,
        tmdb_id: int,
        content_type: str,
        pages: int = 3,
    ) -> list[dict]:
        """Return TMDB similar + recommended titles for a given movie/show.
        Merges both endpoints and deduplicates by id.
        """
        self._assert_content_type(content_type)
        all_items: dict[int, dict] = {}
        for endpoint in ("similar", "recommendations"):
            for page in range(1, pages + 1):
                try:
                    payload = self._get(
                        f"/{content_type}/{tmdb_id}/{endpoint}",
                        {"language": "en-US", "page": page},
                    )
                    if not isinstance(payload, dict):
                        break
                    for item in payload.get("results", []):
                        item_id = item.get("id")
                        if item_id and item_id not in all_items:
                            all_items[item_id] = item
                    if page >= int(payload.get("total_pages", page)):
                        break
                except RuntimeError:
                    break
        return list(all_items.values())

    def discover_content(
        self,
        content_type: str,
        genre_id: int | list[int] | None,
        provider_id: int | None,
        language_code: str | None,
        region: str = "US",
        pages: int = 3,
    ) -> list[dict]:
        self._assert_content_type(content_type)
        all_items: dict[int, dict] = {}
        page_limit = max(1, min(pages, 500))

        for page in range(1, page_limit + 1):
            params: dict[str, str | int] = {
                "language": "en-US",
                "include_adult": "false",
                "sort_by": "popularity.desc",
                "vote_count.gte": 1,
                "watch_region": region,
                "page": page,
            }
            if content_type == CONTENT_TYPE_MOVIE:
                params["include_video"] = "false"

            if genre_id is not None:
                if isinstance(genre_id, list):
                    if genre_id:
                        params["with_genres"] = "|".join(str(g) for g in genre_id)
                else:
                    params["with_genres"] = genre_id
            if provider_id is not None:
                params["with_watch_providers"] = provider_id
                params["with_watch_monetization_types"] = "flatrate|free|ads"
            if language_code is not None:
                params["with_original_language"] = language_code

            payload = self._get(f"/discover/{content_type}", params)
            if not isinstance(payload, dict):
                continue
            for item in payload.get("results", []):
                all_items[item["id"]] = item

            total_pages = int(payload.get("total_pages", page))
            if page >= total_pages:
                break

        return list(all_items.values())


def build_movie_from_tmdb(
    raw_item: dict,
    genre_lookup: dict[int, str],
    selected_platform: str | None,
    content_type: str,
) -> Movie:
    title = raw_item.get("title") if content_type == CONTENT_TYPE_MOVIE else raw_item.get("name")
    title = title or "Unknown Title"
    genre_ids = raw_item.get("genre_ids", [])
    if genre_ids:
        genres = [genre_lookup.get(genre_id) for genre_id in genre_ids]
        genres = [genre for genre in genres if genre]
    else:
        genres = [genre.get("name", "") for genre in raw_item.get("genres", [])]
        genres = [genre for genre in genres if genre]

    mood_tags: list[str] = []
    for genre in genres:
        mood_tags.extend(MOOD_BY_GENRE.get(genre, []))
    mood_tags = list(dict.fromkeys(mood_tags))

    date_key = "release_date" if content_type == CONTENT_TYPE_MOVIE else "first_air_date"
    release_date = raw_item.get(date_key, "")
    year = int(release_date[:4]) if release_date[:4].isdigit() else 0
    rating = float(raw_item.get("vote_average", 0.0))
    language = (raw_item.get("original_language") or "").strip().lower()
    platforms = [selected_platform] if selected_platform else []

    return Movie(
        title=title,
        genres=genres,
        mood_tags=mood_tags,
        platforms=platforms,
        language=language,
        year=year,
        rating=rating,
        tmdb_id=int(raw_item.get("id", 0)),
        content_type=content_type,
        overview=raw_item.get("overview", "") or "",
        poster_url=_image_url(raw_item.get("poster_path"), "w500"),
        backdrop_url=_image_url(raw_item.get("backdrop_path"), "w780"),
    )
