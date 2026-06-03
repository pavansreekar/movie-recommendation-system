"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import { apiRequest } from "../../lib/api";
import { emitWatchHistoryChange } from "../../lib/watchHistorySync";
import { useSessionGuard } from "../../components/useSessionGuard";

const LANG_LABELS = {
  te: "Telugu", hi: "Hindi", en: "English", ta: "Tamil",
  ml: "Malayalam", kn: "Kannada", mr: "Marathi", bn: "Bengali",
  gu: "Gujarati", pa: "Punjabi", ur: "Urdu", ja: "Japanese",
  ko: "Korean", zh: "Chinese", fr: "French", es: "Spanish",
  de: "German", it: "Italian", pt: "Portuguese", ru: "Russian",
};

function langLabel(code) {
  return LANG_LABELS[code] || code.toUpperCase();
}

function EyeCheckIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8s-2.5 4.5-6.5 4.5S1.5 8 1.5 8Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M6.5 8l1.3 1.3 2.2-2.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function WatchlistPage() {
  const session = useSessionGuard();
  const [items, setItems] = useState([]);
  const [removingKey, setRemovingKey] = useState("");
  const [watchingKey, setWatchingKey] = useState("");

  const [genreFilter, setGenreFilter] = useState("");
  const [langFilter, setLangFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  async function loadWatchlist() {
    const payload = await apiRequest("/api/watchlist");
    setItems(payload.watchlist_items || []);
  }

  useEffect(() => {
    if (session.authenticated) loadWatchlist();
  }, [session.authenticated]);

  async function removeItem(item) {
    const itemKey = `${item.content_type}-${item.tmdb_id}`;
    setRemovingKey(itemKey);
    try {
      await apiRequest(`/api/title/${item.content_type}/${item.tmdb_id}/watchlist`, { method: "POST" });
      setItems((prev) => prev.filter((i) => `${i.content_type}-${i.tmdb_id}` !== itemKey));
    } finally {
      setRemovingKey("");
    }
  }

  // Moves the title from watchlist → watched history and removes it from this page
  async function markAsWatched(item) {
    const itemKey = `${item.content_type}-${item.tmdb_id}`;
    if (watchingKey === itemKey) return;
    setWatchingKey(itemKey);
    try {
      const payload = await apiRequest(
        `/api/title/${item.content_type}/${item.tmdb_id}/watch`,
        { method: "POST" }
      );
      // Backend auto-removes from watchlist; update local state immediately
      emitWatchHistoryChange({
        tmdbId: item.tmdb_id,
        contentType: item.content_type,
        isWatched: payload.is_watched,
      });
      setItems((prev) => prev.filter((i) => `${i.content_type}-${i.tmdb_id}` !== itemKey));
    } finally {
      setWatchingKey("");
    }
  }

  // Derive unique filter options from loaded items
  const genreOptions = useMemo(() => {
    const set = new Set();
    items.forEach((item) => (item.genres || []).forEach((g) => g && set.add(g)));
    return [...set].sort();
  }, [items]);

  const langOptions = useMemo(() => {
    const set = new Set();
    items.forEach((item) => item.language && set.add(item.language));
    return [...set].sort((a, b) => langLabel(a).localeCompare(langLabel(b)));
  }, [items]);

  const filtersActive = genreFilter || langFilter || typeFilter;

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (genreFilter && !(item.genres || []).includes(genreFilter)) return false;
      if (langFilter && item.language !== langFilter) return false;
      if (typeFilter && item.content_type !== typeFilter) return false;
      return true;
    });
  }, [items, genreFilter, langFilter, typeFilter]);

  function clearFilters() {
    setGenreFilter("");
    setLangFilter("");
    setTypeFilter("");
  }

  if (session.loading || !session.authenticated) {
    return <div className="loading-screen">Loading watchlist...</div>;
  }

  return (
    <AppShell user={session.user}>
      <section className="hero premium-card compact-hero" style={{ padding: "1.5rem 1.8rem" }}>
        <div className="hero-with-filters">
          <div className="hero-text">
            <span className="section-kicker">Watchlist</span>
            <h1 style={{ margin: "0.55rem 0 0.5rem", fontSize: "2.3rem", lineHeight: 0.98, letterSpacing: "-0.05em" }}>
              Saved for later
            </h1>
            <p style={{ margin: 0 }}>
              Hover a title — hit{" "}
              <span style={{ color: "var(--accent-strong)", fontWeight: 700 }}>×</span>{" "}
              to remove or the{" "}
              <span style={{ color: "var(--accent-strong)", fontWeight: 700 }}>eye</span>{" "}
              to move it to your watched history.
              {items.length > 0 && (
                <span style={{ marginLeft: "0.5rem", color: "var(--accent-strong)", fontWeight: 700 }}>
                  {filtered.length}{filtersActive && filtered.length !== items.length ? ` of ${items.length}` : ""} title{items.length !== 1 ? "s" : ""}
                </span>
              )}
            </p>
          </div>

          {items.length > 0 && (
            <div className="hero-filter-section">
              <span className="filter-bar-label">Filter by</span>

              {/* Type — segmented pill control */}
              <div className="type-pill-group">
                <button className={`type-pill${!typeFilter ? " active" : ""}`} onClick={() => setTypeFilter("")}>All</button>
                <button className={`type-pill${typeFilter === "movie" ? " active" : ""}`} onClick={() => setTypeFilter("movie")}>Movies</button>
                <button className={`type-pill${typeFilter === "tv" ? " active" : ""}`} onClick={() => setTypeFilter("tv")}>TV Shows</button>
              </div>

              <select
                className={`filter-select${genreFilter ? " active" : ""}`}
                value={genreFilter}
                onChange={(e) => setGenreFilter(e.target.value)}
                aria-label="Filter by genre"
              >
                <option value="">All genres</option>
                {genreOptions.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>

              <select
                className={`filter-select${langFilter ? " active" : ""}`}
                value={langFilter}
                onChange={(e) => setLangFilter(e.target.value)}
                aria-label="Filter by language"
              >
                <option value="">All languages</option>
                {langOptions.map((l) => <option key={l} value={l}>{langLabel(l)}</option>)}
              </select>

              {filtersActive && (
                <button className="filter-clear-btn" onClick={clearFilters}>
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
                    <path d="M2 2l6 6M8 2L2 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                  </svg>
                  Clear
                </button>
              )}
            </div>
          )}
        </div>
      </section>

      {items.length === 0 ? (
        <div className="empty-state premium-card">
          No titles in your watchlist yet. Hit the bookmark icon on any recommendation to save it here.
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state premium-card">
          No titles match your current filters.{" "}
          <button
            onClick={clearFilters}
            style={{ background: "none", border: "none", color: "var(--accent-strong)", fontWeight: 700, cursor: "pointer", padding: 0, font: "inherit" }}
          >
            Clear filters
          </button>
        </div>
      ) : (
        <section className="history-poster-grid">
          {filtered.map((item) => {
            const itemKey = `${item.content_type}-${item.tmdb_id}`;
            const isRemoving = removingKey === itemKey;
            const isWatching = watchingKey === itemKey;

            return (
              <article className="history-poster-card" key={itemKey}>
                {/* × remove from watchlist — top-left */}
                <button
                  className="history-remove-button"
                  onClick={() => removeItem(item)}
                  aria-label={`Remove ${item.title} from watchlist`}
                  disabled={isRemoving || isWatching}
                >
                  ×
                </button>

                {/* Eye — move to watched history — top-right */}
                <button
                  className="watchlist-watch-button"
                  onClick={() => markAsWatched(item)}
                  aria-label={`Mark ${item.title} as watched`}
                  title="Mark as watched"
                  disabled={isRemoving || isWatching}
                >
                  <EyeCheckIcon />
                </button>

                <Link
                  href={`/title/${item.content_type}/${item.tmdb_id}`}
                  className="history-poster-link"
                  aria-label={`View ${item.title}`}
                >
                  {item.poster_url
                    ? <img src={item.poster_url} alt={item.title} />
                    : <div className="poster-fallback">{item.title}</div>
                  }
                </Link>
                <div className="history-poster-overlay">
                  <span className="history-poster-title">{item.title}</span>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </AppShell>
  );
}
