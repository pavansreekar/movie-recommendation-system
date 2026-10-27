"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import { apiRequest, requestWithRetry } from "../../lib/api";
import { emitWatchHistoryChange, LIBRARY_EVENT } from "../../lib/watchHistorySync";
import { useSessionGuard } from "../../components/useSessionGuard";
import { PageLoader, PosterSkeleton, SectionError } from "../../components/Loading";

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

export default function HistoryPage() {
  const session = useSessionGuard();
  const [items, setItems] = useState([]);
  const [listState, setListState] = useState("loading");   // "loading" | "ready" | "error"
  const [removingKey, setRemovingKey] = useState("");

  const [genreFilter, setGenreFilter] = useState("");
  const [langFilter, setLangFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [ratedOnly, setRatedOnly] = useState(false);

  async function loadHistory() {
    const payload = await apiRequest("/api/history");
    setItems(payload.watched_items || []);
  }

  // Until the first load lands, show placeholders rather than "No watched history yet".
  function firstLoad() {
    setListState("loading");
    requestWithRetry("/api/history")
      .then((payload) => { setItems(payload.watched_items || []); setListState("ready"); })
      .catch(() => setListState("error"));
  }

  useEffect(() => {
    if (!session.authenticated) return undefined;
    firstLoad();
    const refresh = () => loadHistory().catch(() => {});
    window.addEventListener(LIBRARY_EVENT, refresh);
    return () => window.removeEventListener(LIBRARY_EVENT, refresh);
  }, [session.authenticated]);

  async function removeItem(item) {
    const itemKey = `${item.content_type}-${item.tmdb_id}`;
    setRemovingKey(itemKey);
    try {
      const payload = await apiRequest(`/api/title/${item.content_type}/${item.tmdb_id}/watch`, { method: "POST" });
      emitWatchHistoryChange({
        tmdbId: item.tmdb_id,
        contentType: item.content_type,
        isWatched: payload.is_watched,
      });
      await loadHistory();
    } finally {
      setRemovingKey("");
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

  const ratedCount = useMemo(
    () => items.filter((item) => item.user_rating != null).length,
    [items]
  );

  const filtersActive = genreFilter || langFilter || typeFilter || ratedOnly;

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (genreFilter && !(item.genres || []).includes(genreFilter)) return false;
      if (langFilter && item.language !== langFilter) return false;
      if (typeFilter && item.content_type !== typeFilter) return false;
      if (ratedOnly && item.user_rating == null) return false;
      return true;
    });
  }, [items, genreFilter, langFilter, typeFilter, ratedOnly]);

  function clearFilters() {
    setGenreFilter("");
    setLangFilter("");
    setTypeFilter("");
    setRatedOnly(false);
  }

  if (session.loading || !session.authenticated) {
    return <PageLoader messages={["Opening your watch history", "Gathering your ratings"]} />;
  }

  return (
    <AppShell user={session.user}>
      <section className="hero premium-card compact-hero lib-hero-card">
        <div className="hero-with-filters">
          <div className="hero-text">
            <span className="section-kicker">Library</span>
            <h1 className="lib-hero-title">
              Things you can&apos;t unsee
            </h1>
            <p style={{ margin: 0 }}>
              Your watched history lives here.
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

              <button
                type="button"
                className={`rated-toggle-btn${ratedOnly ? " active" : ""}`}
                onClick={() => setRatedOnly((v) => !v)}
                disabled={ratedCount === 0}
                aria-pressed={ratedOnly}
                title={ratedCount === 0 ? "You haven't rated anything yet" : undefined}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                </svg>
                Rated{ratedCount > 0 ? ` (${ratedCount})` : ""}
              </button>

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

      {listState === "loading" ? (
        <PosterSkeleton count={12} gridClass="history-poster-grid" caption={["Loading your watch history", "Gathering your ratings"]} />
      ) : listState === "error" && items.length === 0 ? (
        <SectionError what="your watch history" onRetry={firstLoad} />
      ) : items.length === 0 ? (
        <div className="empty-state premium-card">No watched history yet.</div>
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
        <section className="history-poster-grid np-reveal">
          {filtered.map((item) => {
            const itemKey = `${item.content_type}-${item.tmdb_id}`;
            const isRemoving = removingKey === itemKey;

            return (
              <article className="history-poster-card" key={itemKey}>
                <button
                  className="history-remove-button"
                  onClick={() => removeItem(item)}
                  aria-label={`Remove ${item.title} from watched history`}
                  disabled={isRemoving}
                >
                  ×
                </button>
                {item.user_rating != null && (
                  <span className="history-rating-badge" title={`You rated this ${item.user_rating}/10`}>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                      <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                    </svg>
                    {item.user_rating}
                  </span>
                )}
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
