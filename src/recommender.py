from __future__ import annotations

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


class MovieRecommender:
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
        watched_keys = {(movie.content_type, movie.tmdb_id) for movie in watched_history}
        genre_preferences = Counter(
            genre_item for movie in watched_history for genre_item in movie.genres
        )
        genres_set = set(genres) if genres else set()

        ranked: list[Recommendation] = []

        for movie in self.catalog:
            if (movie.content_type, movie.tmdb_id) in watched_keys:
                continue

            score = 0.0
            reasons: list[str] = []

            if platform and platform not in movie.platforms:
                continue
            if platform and platform in movie.platforms:
                score += 3.0
                reasons.append(f"Available on {platform}")

            if language and language != movie.language:
                continue
            if language and language == movie.language:
                score += 2.5
                reasons.append(f"Matches your language: {language}")

            if genres_set:
                matched = [g for g in genres_set if g in movie.genres]
                if matched:
                    score += 4.0 * len(matched)
                    reasons.append(f"Matches genre{'s' if len(matched) > 1 else ''}: {', '.join(matched)}")

            history_genre_score = sum(genre_preferences[g] for g in movie.genres)
            if history_genre_score:
                weighted_history_genre_score = min(history_genre_score, 5) * 0.8
                score += weighted_history_genre_score
                reasons.append("Close to your watch history genres")

            # Slight quality prior so ties prefer stronger titles.
            score += movie.rating * 0.15

            if score > 0:
                ranked.append(Recommendation(movie=movie, score=round(score, 2), reasons=reasons))

        ranked.sort(key=lambda rec: rec.score, reverse=True)
        if top_k is None:
            return ranked
        return ranked[:top_k]
