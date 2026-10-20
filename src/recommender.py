from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Movie:
    title: str
    genres: list[str]
    mood_tags: list[str]
    platforms: list[str]
    language: str
    year: int
    rating: float
    tmdb_id: int = 0
    content_type: str = "movie"
    overview: str = ""
    poster_url: str = ""
    backdrop_url: str = ""
    user_rating: float | None = None


@dataclass(frozen=True)
class Recommendation:
    movie: Movie
    score: float
    reasons: list[str]


# ──────────────────────────────────────────────────────────────────────────────
# Feature vector helpers (pure Python, no external ML libraries required)
# ──────────────────────────────────────────────────────────────────────────────

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [0, 1]."""
    return max(0.0, _dot(a, b) / (_norm(a) * _norm(b)))


def _build_feature_vector(
    movie: Movie,
    all_genres: list[str],
    all_languages: list[str],
) -> list[float]:
    """One-hot encode genres + language + normalised rating + year bucket."""
    vec: list[float] = []

    # Genre one-hot (weighted by genre importance)
    for g in all_genres:
        vec.append(1.0 if g in movie.genres else 0.0)

    # Language one-hot
    for lang in all_languages:
        vec.append(1.2 if lang == movie.language else 0.0)

    # Normalised TMDB rating  (0–1 range)
    vec.append((movie.rating or 0.0) / 10.0)

    # Year recency bucket (0–1, newer = higher)
    year = movie.year or 1990
    vec.append(min(1.0, max(0.0, (year - 1980) / 50.0)))

    return vec


def _build_user_profile_vector(
    watch_history: list[Movie],
    all_genres: list[str],
    all_languages: list[str],
) -> list[float] | None:
    """
    Average feature vector of everything the user has watched.
    Returns None when history is empty (can't form a profile).
    """
    if not watch_history:
        return None
    vecs = [_build_feature_vector(m, all_genres, all_languages) for m in watch_history]
    dim = len(vecs[0])
    profile = [0.0] * dim
    for v in vecs:
        for i, val in enumerate(v):
            profile[i] += val
    n = len(vecs)
    return [x / n for x in profile]


# ──────────────────────────────────────────────────────────────────────────────
# Main recommender
# ──────────────────────────────────────────────────────────────────────────────

class MovieRecommender:
    """
    Hybrid recommender — combines:
      1. Rule-based scoring  (genre/platform/language filters + quality prior)
      2. Content-based ML scoring  (cosine similarity between the candidate and
         the user's averaged watch-history feature vector)

    The two scores are blended: final = rule_score + ml_weight * cosine_similarity
    The ML component has no external dependencies (pure Python linear algebra).
    """

    # Weights
    # History-driven weights (W_HISTORY_*, W_ML_SIMILARITY) are naturally zero for a
    # first-time user with no watch history — genre_preferences is empty and
    # user_profile is None — so bumping them only sharpens personalization for
    # returning users; it never changes what a brand-new user sees.
    W_GENRE_MATCH   = 4.0   # per matched explicit genre
    W_PLATFORM      = 3.0
    W_LANGUAGE      = 2.5
    W_HISTORY_CAP   = 8     # max history items counted for genre affinity
    W_HISTORY_UNIT  = 1.0   # per matching watch-history genre count
    W_QUALITY       = 0.15  # TMDB rating multiplier
    W_ML_SIMILARITY = 4.5   # max bonus from cosine similarity to user profile

    def __init__(self, catalog: Iterable[Movie]) -> None:
        self.catalog = list(catalog)

    def recommend(
        self,
        genres: list[str] | None,
        platform: str | None,
        language: str | None,
        watched_history: list[Movie],
        top_k: int | None = 5,
    ) -> list[Recommendation]:

        watched_keys = {(m.content_type, m.tmdb_id) for m in watched_history}
        genre_preferences = Counter(
            g for m in watched_history for g in m.genres
        )
        genres_set = set(genres) if genres else set()

        # ── Build ML profile once (reused for every candidate) ─────────────
        all_genres    = sorted({g for m in self.catalog for g in m.genres})
        all_languages = sorted({m.language for m in self.catalog if m.language})
        user_profile  = _build_user_profile_vector(watched_history, all_genres, all_languages)

        ranked: list[Recommendation] = []

        for movie in self.catalog:
            if (movie.content_type, movie.tmdb_id) in watched_keys:
                continue

            score = 0.0
            reasons: list[str] = []

            # ── Hard filters ────────────────────────────────────────────────
            if platform and platform not in movie.platforms:
                continue
            if language and language != movie.language:
                continue

            # ── Rule-based scoring ──────────────────────────────────────────
            if platform and platform in movie.platforms:
                score += self.W_PLATFORM
                reasons.append(f"Available on {platform}")

            if language and language == movie.language:
                score += self.W_LANGUAGE
                reasons.append(f"Matches your language: {language}")

            if genres_set:
                matched = [g for g in genres_set if g in movie.genres]
                if matched:
                    score += self.W_GENRE_MATCH * len(matched)
                    reasons.append(
                        f"Matches genre{'s' if len(matched) > 1 else ''}: {', '.join(matched)}"
                    )

            history_genre_score = sum(genre_preferences[g] for g in movie.genres)
            if history_genre_score:
                bonus = min(history_genre_score, self.W_HISTORY_CAP) * self.W_HISTORY_UNIT
                score += bonus
                reasons.append("Close to your watch history genres")

            # Quality prior
            score += (movie.rating or 0.0) * self.W_QUALITY

            # ── ML similarity bonus ─────────────────────────────────────────
            if user_profile:
                candidate_vec = _build_feature_vector(movie, all_genres, all_languages)
                sim = _cosine(user_profile, candidate_vec)       # 0.0 – 1.0
                ml_bonus = sim * self.W_ML_SIMILARITY
                score += ml_bonus
                if sim > 0.65 and "watch history" not in " ".join(reasons).lower():
                    reasons.append("Highly aligned with your taste profile")

            if score > 0:
                ranked.append(Recommendation(movie=movie, score=round(score, 2), reasons=reasons))

        ranked.sort(key=lambda rec: rec.score, reverse=True)
        return ranked if top_k is None else ranked[:top_k]
