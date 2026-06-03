"use client";

import { useState, useEffect, useRef, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import AppShell from "../../components/AppShell";
import { apiRequest } from "../../lib/api";
import { emitWatchHistoryChange, WATCH_HISTORY_EVENT } from "../../lib/watchHistorySync";
import { useSessionGuard } from "../../components/useSessionGuard";

function SearchContent() {
  const session = useSessionGuard();
  const searchParams = useSearchParams();
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [manualResults, setManualResults] = useState([]);
  const [manualError, setManualError] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const debounceRef = useRef(null);
  const suppressRef = useRef(false);
  const wrapperRef = useRef(null);
  const didAutoRun = useRef(false);

  // Auto-run search if navigated here with ?q= param
  useEffect(() => {
    const q = searchParams.get("q");
    if (q && !didAutoRun.current) {
      didAutoRun.current = true;
      setQuery(q);
      runSearch(q);
    }
  }, [searchParams]);

  useEffect(() => {
    if (suppressRef.current) {
      suppressRef.current = false;
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (query.trim().length < 2) {
      setSuggestions([]);
      setShowDropdown(false);
      setActiveIndex(-1);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      try {
        const payload = await apiRequest(
          `/api/search/suggest?query=${encodeURIComponent(query.trim())}`
        );
        const results = payload.suggestions || [];
        setSuggestions(results);
        setShowDropdown(results.length > 0);
        setActiveIndex(-1);
      } catch {
        setSuggestions([]);
        setShowDropdown(false);
      }
    }, 150);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  useEffect(() => {
    function onMouseDown(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setShowDropdown(false);
        setActiveIndex(-1);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  useEffect(() => {
    function handleWatchHistoryChange(event) {
      const detail = event.detail || {};
      setManualResults((prev) =>
        prev.map((item) =>
          item.tmdb_id === detail.tmdbId && item.content_type === detail.contentType
            ? { ...item, is_watched: detail.isWatched }
            : item
        )
      );
    }
    window.addEventListener(WATCH_HISTORY_EVENT, handleWatchHistoryChange);
    return () => window.removeEventListener(WATCH_HISTORY_EVENT, handleWatchHistoryChange);
  }, []);

  async function runSearch(searchQuery) {
    setError("");
    setManualError("");
    setSuggestions([]);
    setShowDropdown(false);
    setActiveIndex(-1);
    setLoading(true);
    try {
      const payload = await apiRequest("/api/search/results", {
        method: "POST",
        body: JSON.stringify({ query: searchQuery }),
      });
      setManualResults(payload.results || []);
      setManualError(payload.error || "");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function handleFormSubmit(event) {
    event.preventDefault();
    const q = activeIndex >= 0 && suggestions[activeIndex]
      ? suggestions[activeIndex].title
      : query.trim();
    if (q) {
      suppressRef.current = true;
      setQuery(q);
      runSearch(q);
    }
  }

  function handleSuggestionClick(suggestion) {
    suppressRef.current = true;
    setQuery(suggestion.title);
    runSearch(suggestion.title);
  }

  function handleKeyDown(e) {
    if (!showDropdown || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, -1));
    } else if (e.key === "Escape") {
      setShowDropdown(false);
      setActiveIndex(-1);
    }
  }

  async function toggleWatch(movie) {
    try {
      const payload = await apiRequest(`/api/title/${movie.content_type}/${movie.tmdb_id}/watch`, { method: "POST" });
      setManualResults((prev) =>
        prev.map((item) =>
          item.tmdb_id === movie.tmdb_id && item.content_type === movie.content_type
            ? { ...item, is_watched: payload.is_watched }
            : item
        )
      );
      emitWatchHistoryChange({
        tmdbId: movie.tmdb_id,
        contentType: movie.content_type,
        isWatched: payload.is_watched,
      });
    } catch (err) {
      setError(err.message);
    }
  }

  if (session.loading || !session.authenticated) {
    return <div className="loading-screen">Loading...</div>;
  }

  return (
    <AppShell user={session.user}>
      {error ? <div className="alert alert-warning">{error}</div> : null}

      <section
        className="premium-card panel-pad"
        style={{ alignSelf: "start", position: "relative", zIndex: 10, isolation: "isolate", overflow: "visible" }}
      >
        <span className="section-kicker">Search window</span>
        <h2 style={{ margin: "0.5rem 0 1.35rem", fontSize: "2.15rem", lineHeight: 0.97, letterSpacing: "-0.05em" }}>
          From Cult Classics To Current Hits
        </h2>

        <form
          className="form-grid"
          style={{ gridTemplateColumns: "1fr auto", alignItems: "end" }}
          onSubmit={handleFormSubmit}
        >
          <label style={{ position: "relative" }} ref={wrapperRef}>
            <span>Title</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => suggestions.length > 0 && setShowDropdown(true)}
              placeholder="Search a movie or TV show…"
              autoComplete="off"
            />
            {showDropdown && suggestions.length > 0 && (
              <ul style={{
                position: "absolute",
                top: "calc(100% + 6px)",
                left: 0,
                right: 0,
                zIndex: 9999,
                margin: 0,
                padding: "0.35rem",
                listStyle: "none",
                background: "var(--nav-suggest-bg)",
                border: "1px solid var(--line-strong)",
                borderRadius: "var(--radius-lg)",
                overflow: "hidden",
                boxShadow: "var(--shadow)",
                backdropFilter: "blur(24px)",
              }}>
                {suggestions.map((s, i) => (
                  <li
                    key={`${s.content_type}-${s.tmdb_id}`}
                    onMouseDown={(e) => { e.preventDefault(); handleSuggestionClick(s); }}
                    onMouseEnter={() => setActiveIndex(i)}
                    onMouseLeave={() => setActiveIndex(-1)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.8rem",
                      padding: "0.6rem 0.7rem",
                      cursor: "pointer",
                      borderRadius: "10px",
                      background: i === activeIndex ? "rgba(232,162,58,0.10)" : "transparent",
                      transition: "background 0.1s",
                    }}
                  >
                    {s.poster_url ? (
                      <img src={s.poster_url} alt={s.title}
                        style={{ width: 30, height: 44, borderRadius: 7, objectFit: "cover", flexShrink: 0 }} />
                    ) : (
                      <div style={{ width: 30, height: 44, borderRadius: 7, background: "rgba(255,255,255,0.05)", flexShrink: 0 }} />
                    )}
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: "0.88rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {s.title}
                      </div>
                      <div style={{ fontSize: "0.74rem", color: "var(--muted)", marginTop: "0.15rem" }}>
                        {s.content_label}{s.year ? ` · ${s.year}` : ""}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </label>

          <div className="form-actions">
            <button className="button button-primary" disabled={loading}>
              {loading ? "Searching…" : "Search"}
            </button>
          </div>
        </form>
      </section>

      {manualError ? <div className="alert alert-warning">{manualError}</div> : null}

      {manualResults.map((item) => (
        <article
          className="content-card premium-card reveal-card"
          key={`${item.content_type}-${item.tmdb_id}`}
        >
          <div className="poster-wrap">
            {item.poster_url
              ? <img src={item.poster_url} alt={item.title} />
              : <div className="poster-fallback">No poster</div>
            }
          </div>
          <div className="content-body">
            <div className="content-meta">
              <span>{item.content_label}</span>
              {item.year ? (
                <>
                  <span style={{ color: "rgba(255,255,255,0.2)" }}>·</span>
                  <span>{item.year}</span>
                </>
              ) : null}
            </div>
            <h3>{item.title}</h3>
            <p>{item.overview || "No synopsis available."}</p>
            <div className="action-row">
              <Link className="button button-secondary" href={`/title/${item.content_type}/${item.tmdb_id}`}>
                View details
              </Link>
              <button
                className={`button ${item.is_watched ? "button-secondary" : "button-primary"}`}
                onClick={() => toggleWatch(item)}
                disabled={item.is_watched}
              >
                {item.is_watched ? "In watched history" : "Add to watched history"}
              </button>
            </div>
          </div>
        </article>
      ))}
    </AppShell>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<div className="loading-screen">Loading...</div>}>
      <SearchContent />
    </Suspense>
  );
}
