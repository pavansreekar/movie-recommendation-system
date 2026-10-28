from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .recommender import Movie

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_HOST = "api.themoviedb.org"
# TMDB serves the same API on a second hostname. Some networks (several Indian ISPs)
# block the main one but not this, so the client moves to whichever of the two answers.
# TMDB_API_HOST only chooses which is tried first.
TMDB_ALTERNATE_HOST = "api.tmdb.org"
TMDB_API_PREFIX = "/3"

# Slow-changing reference data can be cached far longer than listings.
STATIC_PATH_PREFIXES = ("/configuration", "/genre/", "/watch/providers/")
STATIC_CACHE_TTL_SECONDS = 24 * 60 * 60
# Detailed title payloads are large once parsed; this keeps the cache well inside the free
# Render instance's 512 MB.
MAX_CACHE_ENTRIES = 800
# Once a call has used up its retries on network errors, further calls fail at once for
# this long instead of each waiting out its own timeouts. One page or chat reply makes
# many calls, and during an outage those waits used to stack up into minutes.
UNREACHABLE_COOLDOWN_SECONDS = 10

# Parallel TMDB fetches. Kept small: the free server has a fraction of a CPU, and 16 busy
# threads parsing responses left no time for logins or Render's 5-second health check.
_shared_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="tmdb")
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
    priority: int = 9999    # TMDB/JustWatch display order within the region (lower = more prominent)


@dataclass(frozen=True)
class ContentDetails:
    movie: Movie
    genres: list[str]
    cast: list[str]         # names only – kept for backwards compat
    cast_full: list[dict]   # [{id, name, character, profile_url}]
    providers: list[str]
    original_rating: float
    certification: str      # e.g. "U/A", "A", "PG-13" — "" when TMDB has none listed
    trailer: dict | None    # {key, name, type, site, official} for the best YouTube trailer, if any
    info: dict              # extra facts: directors, creators, runtime_minutes, seasons, episodes, release_date, tagline, status, collection_id, collection_name


def _image_url(path: str | None, size: str) -> str:
    if not path:
        return ""
    return f"{TMDB_IMAGE_BASE_URL}/{size}{path}"


def _extract_certification(payload: dict, content_type: str, region: str) -> str:
    """Best-effort maturity rating (e.g. 'U/A', 'A', 'PG-13') for the given region.
    Movies carry it under release_dates (per-country list of release events, each with
    its own certification); TV shows carry a single rating per country under
    content_ratings. Falls back to the US rating when the requested region has none —
    TMDB's Indian coverage is patchy for older or smaller titles.
    """
    by_country: dict[str, str] = {}
    if content_type == CONTENT_TYPE_MOVIE:
        for entry in payload.get("release_dates", {}).get("results", []):
            country = entry.get("iso_3166_1", "")
            if not country or country in by_country:
                continue
            for release in entry.get("release_dates", []):
                cert = (release.get("certification") or "").strip()
                if cert:
                    by_country[country] = cert
                    break
    else:
        for entry in payload.get("content_ratings", {}).get("results", []):
            country = entry.get("iso_3166_1", "")
            rating = (entry.get("rating") or "").strip()
            if country and rating:
                by_country[country] = rating
    return by_country.get(region) or by_country.get("US") or ""


def _extract_info(payload: dict, content_type: str) -> dict:
    """Facts about a title that the listing fields don't carry (who made it, how long it is)."""
    crew = payload.get("credits", {}).get("crew", [])
    directors = list(dict.fromkeys(c.get("name", "") for c in crew if c.get("job") == "Director" and c.get("name")))
    creators = [c.get("name", "") for c in payload.get("created_by", []) if c.get("name")]
    release_date = payload.get("release_date") if content_type == CONTENT_TYPE_MOVIE else payload.get("first_air_date")
    return {
        "directors": directors[:3],
        "creators": creators[:3],
        "runtime_minutes": payload.get("runtime") or None,
        "seasons": payload.get("number_of_seasons") or None,
        "episodes": payload.get("number_of_episodes") or None,
        "release_date": release_date or "",
        "tagline": (payload.get("tagline") or "").strip(),
        "status": (payload.get("status") or "").strip(),
        "collection_id": (payload.get("belongs_to_collection") or {}).get("id"),
        "collection_name": (payload.get("belongs_to_collection") or {}).get("name") or "",
    }


# Languages requested for a title's videos. TMDB only returns videos tagged with the
# request language unless told otherwise, and many non-English titles only have a
# trailer tagged in their own language.
VIDEO_LANGUAGES = "en,null,hi,te,ta,ml,kn,bn,mr,pa,gu,ko,ja,zh,fr,es,de,it,pt,ru,tr,ar,id,th"


# Re-releases, fan cuts and special versions are still tagged "Trailer" on TMDB.
_TRAILER_VARIANT = re.compile(
    r"\bfan\b|re-?release|anniversary|\bimax\b|b&w|black.?and.?white|remaster|\bdub(bed)?\b"
    r"|tv spot|\bspot\b|sneak peek|announcement|\bversion\b|\bcut\b|red band|reaction|\brecap\b",
    re.IGNORECASE,
)


def _pick_trailer(payload: dict) -> dict | None:
    """Best YouTube trailer for a title. In order: a real Trailer before a Teaser, official
    uploads, the main trailer rather than a re-release/fan/special-version cut, English
    (then the title's own language), a plainly named "Official Trailer", then newest."""
    videos = payload.get("videos", {}).get("results", [])
    original_language = payload.get("original_language") or ""
    type_rank = {"Trailer": 0, "Teaser": 1}
    candidates = [
        v for v in videos
        if v.get("site") == "YouTube" and v.get("key") and v.get("type") in type_rank
    ]
    if not candidates:
        return None

    def rank(video: dict) -> tuple:
        name = video.get("name") or ""
        language = video.get("iso_639_1") or ""
        return (
            type_rank[video["type"]],
            0 if video.get("official") else 1,
            1 if _TRAILER_VARIANT.search(name) else 0,
            0 if language == "en" else 1 if language == original_language else 2,
            0 if re.search(r"official trailer|main trailer|final trailer", name, re.IGNORECASE) else 1,
        )

    # Stable sorts: newest first, then by the ranked criteria.
    candidates.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    candidates.sort(key=rank)
    best = candidates[0]
    return {
        "key": best["key"],
        "name": (best.get("name") or "").strip() or "Trailer",
        "type": best["type"],
        "site": "YouTube",
        "official": bool(best.get("official")),
    }


class TMDBClient:
    def __init__(
        self,
        api_key: str,
        cache_ttl_seconds: int = 600,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.75,
        request_timeout_seconds: float = 5.0,
        host: str = TMDB_HOST,
    ) -> None:
        self.api_key = api_key
        self.host = host
        self._hosts = list(dict.fromkeys([host, TMDB_HOST, TMDB_ALTERNATE_HOST]))
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._cache: dict[str, tuple[float, dict | list]] = {}
        self._cache_lock = threading.Lock()
        self._local = threading.local()
        self._unreachable_until = 0.0
        self._unreachable_reason = ""
        self._ott_cache: dict[str, tuple[float, list[dict]]] = {}

    def _ttl_for(self, path: str) -> float:
        if path.startswith(STATIC_PATH_PREFIXES):
            return STATIC_CACHE_TTL_SECONDS
        return self.cache_ttl_seconds

    def _connection(self) -> http.client.HTTPSConnection:
        conn = getattr(self._local, "conn", None)
        if conn is not None and getattr(self._local, "conn_host", None) != self.host:
            self._drop_connection()     # the client has moved to TMDB's other hostname
            conn = None
        if conn is None:
            conn = http.client.HTTPSConnection(self.host, timeout=self.request_timeout_seconds)
            self._local.conn = conn
            self._local.conn_host = self.host
        return conn

    @property
    def unreachable(self) -> bool:
        """True for a short while after TMDB could not be reached at all."""
        return time.time() < self._unreachable_until

    def _drop_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._local.conn = None

    def _fetch_once(self, path: str, query: str) -> tuple[int, dict[str, str], bytes]:
        """One GET over a kept-alive connection; reconnects once if the socket went stale."""
        target = f"{TMDB_API_PREFIX}{path}?{query}"
        headers = {"accept": "application/json", "User-Agent": "movie-bot/1.0"}
        for stale_retry in (True, False):
            conn = self._connection()
            try:
                conn.request("GET", target, headers=headers)
                response = conn.getresponse()
                body = response.read()
                if response.getheader("Connection", "").lower() == "close":
                    self._drop_connection()
                return response.status, dict(response.getheaders()), body
            except TimeoutError:
                # Not a stale socket — a fresh connection would only wait just as long.
                self._drop_connection()
                raise
            except (http.client.HTTPException, ConnectionError, OSError):
                self._drop_connection()
                if not stale_retry:
                    raise
        raise RuntimeError("unreachable")

    def map_parallel(self, func, items: list) -> list:
        """Run func over items concurrently, preserving order. Failures come back as None."""
        def _safe(item):
            try:
                return func(item)
            except Exception:
                return None
        return list(_shared_pool.map(_safe, items))

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
            host=os.getenv("TMDB_API_HOST", "").strip() or TMDB_HOST,
        )

    def _assert_content_type(self, content_type: str) -> None:
        if content_type not in SUPPORTED_CONTENT_TYPES:
            raise ValueError(f"Unsupported content type: {content_type}")

    def _network_error_message(self, reason: object) -> str:
        reason_text = str(reason).strip() or "request timed out"
        return (
            f"TMDB is currently unreachable from this network (tried {' and '.join(self._hosts)}). "
            f"Connection detail: {reason_text}. "
            "If you are in India, try Cloudflare DNS (1.1.1.1), Google DNS (8.8.8.8), "
            "Secure DNS or a VPN."
        )

    def _get(self, path: str, params: dict[str, str | int]) -> dict | list:
        query_params = {"api_key": self.api_key, **params}
        query = urllib.parse.urlencode(query_params)
        cache_key = f"{path}?{query}"
        now = time.time()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._ttl_for(path):
            return cached[1]
        if now < self._unreachable_until:
            raise RuntimeError(self._network_error_message(self._unreachable_reason))

        last_error: Exception | None = None
        tried_hosts = {self.host}
        attempt = 0
        while attempt < self.max_retries:
            try:
                status, headers, body = self._fetch_once(path, query)
            except (TimeoutError, OSError, http.client.HTTPException) as error:
                last_error = error
                other_host = next((h for h in self._hosts if h not in tried_hosts), None)
                if other_host:
                    # Blocked or down on this hostname: TMDB's other one serves the same
                    # API. Later calls start there too.
                    tried_hosts.add(other_host)
                    self.host = other_host
                    continue
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff_seconds * (2**attempt))
                    attempt += 1
                    continue
                self._unreachable_reason = str(error)
                self._unreachable_until = time.time() + UNREACHABLE_COOLDOWN_SECONDS
                raise RuntimeError(self._network_error_message(str(error))) from error

            self._unreachable_until = 0.0   # any answer at all means TMDB is reachable

            if status == 200:
                payload = json.loads(body.decode("utf-8"))
                with self._cache_lock:
                    if len(self._cache) >= MAX_CACHE_ENTRIES:
                        oldest = sorted(self._cache.items(), key=lambda kv: kv[1][0])
                        for key, _ in oldest[: MAX_CACHE_ENTRIES // 4]:
                            self._cache.pop(key, None)
                    self._cache[cache_key] = (time.time(), payload)
                return payload
            if status == 429 and attempt < self.max_retries - 1:
                retry_after = int(headers.get("Retry-After", "1") or "1")
                time.sleep(max(1, retry_after))
                attempt += 1
                continue
            if status >= 500 and attempt < self.max_retries - 1:
                time.sleep(self.retry_backoff_seconds * (2**attempt))
                attempt += 1
                continue
            raise RuntimeError(
                f"TMDB request failed ({status}): {body.decode('utf-8', errors='ignore')}"
            )

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
        def _fetch(page: int):
            return self._get(
                f"/watch/providers/{content_type}",
                {"language": "en-US", "watch_region": region, "page": page},
            )

        first = _fetch(1)
        payloads = [first]
        if isinstance(first, dict):
            total_pages = min(int(first.get("total_pages", 1)), pages)
            if total_pages > 1:
                payloads += self.map_parallel(_fetch, list(range(2, total_pages + 1)))
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for item in payload.get("results", []):
                priorities = item.get("display_priorities") or {}
                provider = Provider(
                    provider_id=item["provider_id"],
                    provider_name=item["provider_name"],
                    priority=int(priorities.get(region, item.get("display_priority", 9999))),
                )
                all_providers[provider.provider_id] = provider
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

    def search_keywords(self, query: str, limit: int = 10) -> list[dict]:
        """TMDB keyword tags matching `query`, as [{"id", "name"}]. Keywords are how TMDB
        marks what a title is about ("time travel", "heist", "james bond")."""
        try:
            payload = self._get("/search/keyword", {"query": query, "page": 1})
        except RuntimeError:
            return []
        results = payload.get("results", []) if isinstance(payload, dict) else []
        return [r for r in results if isinstance(r, dict) and r.get("id") and r.get("name")][:limit]

    def get_collection_parts(self, collection_id: int) -> list[dict]:
        """The films in a TMDB collection (a series such as "The Dark Knight Collection")."""
        try:
            payload = self._get(f"/collection/{collection_id}", {"language": "en-US"})
        except RuntimeError:
            return []
        parts = payload.get("parts", []) if isinstance(payload, dict) else []
        return [p for p in parts if isinstance(p, dict) and p.get("id")]

    def get_person_details(self, person_id: int) -> dict:
        """Fetch full person details including biography and top credits."""
        try:
            payload = self._get(
                f"/person/{person_id}",
                {"language": "en-US", "append_to_response": "combined_credits"},
            )
        except RuntimeError:
            return {}
        if not isinstance(payload, dict):
            return {}

        credits = payload.get("combined_credits", {})
        cast_credits = credits.get("cast", [])
        crew_credits = credits.get("crew", [])

        # Build filmography: cast roles + directing/creator credits, deduped
        seen: set[tuple] = set()
        filmography: list[dict] = []
        for item in cast_credits:
            key = (item.get("media_type"), item.get("id"))
            if key not in seen and item.get("media_type") in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
                seen.add(key)
                filmography.append({
                    "tmdb_id": item.get("id"),
                    "content_type": item.get("media_type"),
                    "title": item.get("title") or item.get("name") or "",
                    "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
                    "character": item.get("character", ""),
                    "poster_url": _image_url(item.get("poster_path"), "w185"),
                    "popularity": float(item.get("popularity", 0)),
                    "rating": float(item.get("vote_average", 0)),
                })
        for item in crew_credits:
            if item.get("job") not in ("Director", "Creator", "Writer"):
                continue
            key = (item.get("media_type"), item.get("id"))
            if key not in seen and item.get("media_type") in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
                seen.add(key)
                filmography.append({
                    "tmdb_id": item.get("id"),
                    "content_type": item.get("media_type"),
                    "title": item.get("title") or item.get("name") or "",
                    "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
                    "character": item.get("job", ""),
                    "poster_url": _image_url(item.get("poster_path"), "w185"),
                    "popularity": float(item.get("popularity", 0)),
                    "rating": float(item.get("vote_average", 0)),
                })
        filmography.sort(key=lambda x: -x["popularity"])

        return {
            "id": payload.get("id"),
            "name": payload.get("name", ""),
            "biography": payload.get("biography", ""),
            "birthday": payload.get("birthday"),
            "deathday": payload.get("deathday"),
            "place_of_birth": payload.get("place_of_birth"),
            "known_for_department": payload.get("known_for_department", "Acting"),
            "profile_url": _image_url(payload.get("profile_path"), "w342"),
            "popularity": float(payload.get("popularity", 0)),
            "filmography": filmography[:30],
        }

    def search_person(self, query: str, limit: int = 3) -> list[dict]:
        """Search for a person by name. Returns raw TMDB person objects sorted by popularity."""
        try:
            payload = self._get(
                "/search/person",
                {"query": query, "language": "en-US", "include_adult": "false"},
            )
        except RuntimeError:
            return []
        if not isinstance(payload, dict):
            return []
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []
        results.sort(key=lambda p: -float(p.get("popularity", 0)))
        return results[:limit]

    def get_person_credits(self, person_id: int, limit: int = 20, crew_only: bool = False) -> list[dict]:
        """A person's most popular works. By default acting credits plus anything they
        directed/created; `crew_only` returns just directing/creating/writing credits
        (for directors and writers, whose "cast" credits are mostly interviews)."""
        try:
            payload = self._get(
                f"/person/{person_id}/combined_credits",
                {"language": "en-US"},
            )
        except RuntimeError:
            return []
        if not isinstance(payload, dict):
            return []
        cast = [] if crew_only else payload.get("cast", [])
        crew = payload.get("crew", [])
        crew_jobs = (
            ("Director", "Creator", "Writer", "Screenplay", "Story")
            if crew_only else ("Director", "Creator", "Executive Producer")
        )
        # Merge and dedupe by (media_type, id)
        seen: set[tuple] = set()
        merged: list[dict] = []
        for item in cast:
            key = (item.get("media_type"), item.get("id"))
            if key not in seen and item.get("media_type") in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
                seen.add(key)
                merged.append(item)
        for item in crew:
            if item.get("job") not in crew_jobs:
                continue
            key = (item.get("media_type"), item.get("id"))
            if key not in seen and item.get("media_type") in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
                seen.add(key)
                merged.append(item)
        merged.sort(key=lambda x: -float(x.get("popularity", 0)))
        return merged[:limit]

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
        # release_dates (movie) / content_ratings (tv) carry the per-country certification —
        # each is only valid on its own endpoint, so the append list depends on content_type.
        cert_append = "release_dates" if content_type == CONTENT_TYPE_MOVIE else "content_ratings"
        payload = self._get(
            f"/{content_type}/{tmdb_id}",
            {
                "language": "en-US",
                "append_to_response": f"credits,watch/providers,videos,{cert_append}",
                "include_video_language": VIDEO_LANGUAGES,
            },
        )
        if not isinstance(payload, dict):
            raise RuntimeError("TMDB returned an unexpected detail payload.")

        genres = [genre.get("name", "") for genre in payload.get("genres", [])]
        genres = [genre for genre in genres if genre]

        cast_entries = payload.get("credits", {}).get("cast", [])
        cast_full = [
            {
                "id": entry.get("id"),
                "name": entry.get("name", ""),
                "character": entry.get("character", ""),
                "profile_url": _image_url(entry.get("profile_path"), "w185"),
            }
            for entry in cast_entries[:10]
            if entry.get("name")
        ]
        cast = [c["name"] for c in cast_full]

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
            cast_full=cast_full,
            providers=provider_names,
            original_rating=float(payload.get("vote_average", 0.0)),
            certification=_extract_certification(payload, content_type, region),
            trailer=_pick_trailer(payload),
            info=_extract_info(payload, content_type),
        )

    def get_ott_releases(
        self, region: str = "IN", days: int = 60, provider_ids: list[int] | None = None
    ) -> list[dict]:
        """Recent arrivals on the given streaming providers in a region.
        `provider_ids` should be the region's main subscription services; when omitted
        the long-standing India set is used."""
        from datetime import datetime, timedelta

        today = datetime.now()
        date_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        if provider_ids:
            provider_filter = "|".join(str(pid) for pid in provider_ids)
        else:
            # Netflix|Amazon Prime Video|JioHotstar|Zee5|Sony Liv|aha|Sun Nxt
            provider_filter = "8|119|2336|232|237|532|309"

        cache_key = f"{region}:{days}:{date_to}:{provider_filter}"
        cached = self._ott_cache.get(cache_key)
        if cached and time.time() - cached[0] < 1800:
            return cached[1]

        # India: query each regional language separately (TMDB accepts one code per
        # filter) so regional cinema isn't drowned out. Elsewhere: no language filter.
        target_languages: list[str | None] = (
            ["te", "hi", "ta", "kn", "ml", "en"] if region == "IN" else [None]
        )
        max_pages = 3

        def _fetch(job: tuple[str, str | None, int]):
            content_type, lang, page = job
            date_gte = "primary_release_date.gte" if content_type == CONTENT_TYPE_MOVIE else "first_air_date.gte"
            date_lte = "primary_release_date.lte" if content_type == CONTENT_TYPE_MOVIE else "first_air_date.lte"
            if lang is None:
                sort_field = "popularity.desc"
            else:
                sort_field = "primary_release_date.desc" if content_type == CONTENT_TYPE_MOVIE else "first_air_date.desc"
            params: dict[str, str | int] = {
                "language": "en-US",
                "sort_by": sort_field,
                "include_adult": "false",
                "watch_region": region,
                "with_watch_providers": provider_filter,
                "with_watch_monetization_types": "flatrate",
                "page": page,
                date_gte: date_from,
                date_lte: date_to,
            }
            if lang is not None:
                params["with_original_language"] = lang
            if content_type == CONTENT_TYPE_MOVIE:
                params["include_video"] = "false"
            return self._get(f"/discover/{content_type}", params)

        all_items: dict[str, dict] = {}

        def _ingest(job: tuple[str, str, int], payload) -> None:
            if not isinstance(payload, dict):
                return
            for item in payload.get("results", []):
                item_copy = dict(item)
                item_copy["_content_type"] = job[0]
                all_items[f"{job[0]}-{item['id']}"] = item_copy

        # Round 1: page 1 for every (type, language) at once.
        first_jobs = [
            (ct, lang, 1)
            for ct in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)
            for lang in target_languages
        ]
        first_payloads = self.map_parallel(lambda j: _fetch(j), first_jobs)
        # Round 2: any further pages, again all at once.
        more_jobs: list[tuple[str, str, int]] = []
        for job, payload in zip(first_jobs, first_payloads):
            _ingest(job, payload)
            if isinstance(payload, dict):
                total_pages = min(int(payload.get("total_pages", 1)), max_pages)
                more_jobs += [(job[0], job[1], pg) for pg in range(2, total_pages + 1)]
        for job, payload in zip(more_jobs, self.map_parallel(lambda j: _fetch(j), more_jobs)):
            _ingest(job, payload)

        # "Recent" is already enforced by the date_from/date_to window above — every item
        # here released within the last `days` days. Within that window, order by
        # popularity descending (not by language — that used to group all Telugu titles
        # before any Hindi ones regardless of how popular each was).
        sorted_items = sorted(
            all_items.values(),
            key=lambda x: float(x.get("popularity") or 0.0),
            reverse=True,
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

        if results:
            self._ott_cache[cache_key] = (time.time(), results)
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
        recommendations_first: bool = False,
    ) -> list[dict]:
        """Return TMDB similar + recommended titles for a given movie/show, deduplicated
        by id and kept in TMDB's order. `recommendations_first` leads with the
        "recommendations" list (what viewers of this title also liked — the stronger
        signal) ahead of the keyword-based "similar" list.
        """
        self._assert_content_type(content_type)
        all_items: dict[int, dict] = {}
        endpoints = ("recommendations", "similar") if recommendations_first else ("similar", "recommendations")
        jobs = [(ep, pg) for ep in endpoints for pg in range(1, pages + 1)]
        payloads = self.map_parallel(
            lambda j: self._get(
                f"/{content_type}/{tmdb_id}/{j[0]}", {"language": "en-US", "page": j[1]}
            ),
            jobs,
        )
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for item in payload.get("results", []):
                item_id = item.get("id")
                if item_id and item_id not in all_items:
                    all_items[item_id] = item
        return list(all_items.values())

    def discover_content(
        self,
        content_type: str,
        genre_id: int | list[int] | None,
        provider_id: int | None,
        language_code: str | None,
        region: str = "US",
        pages: int = 3,
        cast_id: int | list[int] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        runtime_max: int | None = None,
        keyword_ids: list[int] | None = None,
        sort_by: str = "popularity.desc",
    ) -> list[dict]:
        self._assert_content_type(content_type)
        all_items: dict[int, dict] = {}
        page_limit = max(1, min(pages, 500))

        def _params(page: int) -> dict[str, str | int]:
            params: dict[str, str | int] = {
                "language": "en-US",
                "include_adult": "false",
                "sort_by": sort_by,
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
            if cast_id is not None:
                # "|" = OR — matches if ANY of these people are in the cast
                if isinstance(cast_id, list):
                    if cast_id:
                        params["with_cast"] = "|".join(str(c) for c in cast_id)
                else:
                    params["with_cast"] = cast_id
            date_field = "primary_release_date" if content_type == CONTENT_TYPE_MOVIE else "first_air_date"
            if year_from:
                params[f"{date_field}.gte"] = f"{int(year_from)}-01-01"
            if year_to:
                params[f"{date_field}.lte"] = f"{int(year_to)}-12-31"
            if keyword_ids:
                params["with_keywords"] = "|".join(str(k) for k in keyword_ids)   # "|" = any of them
            if runtime_max and content_type == CONTENT_TYPE_MOVIE:
                params["with_runtime.lte"] = int(runtime_max)
                params["with_runtime.gte"] = 40   # drop shorts and entries with no runtime recorded
            return params

        first = self._get(f"/discover/{content_type}", _params(1))
        payloads = [first]
        if isinstance(first, dict) and page_limit > 1:
            last_page = min(int(first.get("total_pages", 1)), page_limit)
            if last_page > 1:
                for payload in self.map_parallel(
                    lambda pg: self._get(f"/discover/{content_type}", _params(pg)),
                    list(range(2, last_page + 1)),
                ):
                    payloads.append(payload)
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for item in payload.get("results", []):
                all_items[item["id"]] = item

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
