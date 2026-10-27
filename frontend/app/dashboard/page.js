"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import { apiRequest } from "../../lib/api";
import { useSessionGuard } from "../../components/useSessionGuard";
import { useRouter } from "next/navigation";

// Right after the server wakes up or restarts, these TMDB-backed sections can take a while
// or fail once, so each one is retried a few times before it gives up.
async function loadWithRetry(path, attempts = 3) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await apiRequest(path, { timeoutMs: 45000 });
    } catch (error) {
      if (attempt >= attempts) throw error;
      await new Promise((resolve) => setTimeout(resolve, 3000 * attempt));
    }
  }
}

function SectionError({ what, onRetry }) {
  return (
    <div className="section-error">
      <span>Couldn't load {what}. The server may still be starting up.</span>
      <button type="button" className="section-error-btn" onClick={onRetry}>Try again</button>
    </div>
  );
}

export default function HomePage() {
  const session = useSessionGuard();
  const router = useRouter();
  const [watchedCount, setWatchedCount] = useState(null);
  // Each section: undefined = loading, "error" = failed after retries, otherwise loaded.
  const [trending, setTrending] = useState(undefined);
  const [ott, setOtt] = useState(undefined);   // { releases, providers }
  const [todaysPick, setTodaysPick] = useState(undefined); // null = no pick today

  function loadTrending() {
    setTrending(undefined);
    loadWithRetry("/api/search/trending")
      .then((payload) => setTrending(payload.trending || []))
      .catch(() => setTrending("error"));
  }
  function loadOtt() {
    setOtt(undefined);
    loadWithRetry("/api/dashboard/ott-releases")
      .then((payload) => setOtt({ releases: payload.releases || [], providers: payload.providers || [] }))
      .catch(() => setOtt("error"));
  }
  function loadTodaysPick() {
    setTodaysPick(undefined);
    loadWithRetry("/api/dashboard/today-pick")
      .then((payload) => setTodaysPick(payload.pick || null))
      .catch(() => setTodaysPick("error"));
  }

  useEffect(() => {
    if (!session.authenticated) return;
    loadWithRetry("/api/history")
      .then((payload) => setWatchedCount((payload.watched_items || []).length))
      .catch(() => setWatchedCount(null));
    loadTrending();
    loadOtt();
    loadTodaysPick();
  }, [session.authenticated]);

  const ottReleases = ott && ott !== "error" ? ott.releases : [];
  const ottProviders = ott && ott !== "error" ? ott.providers : [];

  if (session.loading) {
    return <div className="loading-screen">Loading...</div>;
  }
  if (!session.authenticated) {
    return <div className="loading-screen">Redirecting...</div>;
  }

  return (
    <AppShell user={session.user}>
      {/* Hero */}
      <section className="premium-card dashboard-hero-card">
        <div className="dashboard-hero-glow" />
        <span className="section-kicker">Home</span>
        <h1 className="dashboard-hero-title">
          Good to see you,{" "}
          <span className="dashboard-hero-name">{session.user?.username || "there"}</span>.
        </h1>
        <p style={{ margin: 0, fontSize: "0.88rem", color: "var(--muted)", lineHeight: 1.65 }}>
          Your personal movie &amp; TV recommendation workspace.
        </p>
        {watchedCount !== null && (
          <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", marginTop: "0.35rem" }}>
            <span className="score-badge">
              <span className="score-dot" />
              {watchedCount} title{watchedCount !== 1 ? "s" : ""} watched
            </span>
            <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>
              {watchedCount === 0
                ? "— start adding titles to power your recommendations"
                : "— powering your recommendations"}
            </span>
          </div>
        )}
      </section>

      {/* ── Today's Pick ── */}
      {todaysPick === undefined ? (
        /* Loading skeleton */
        <div className="todays-pick-skeleton" />
      ) : todaysPick === "error" ? (
        <SectionError what="Today's Pick" onRetry={loadTodaysPick} />
      ) : todaysPick ? (
        <>
          <section className="section-head">
            <span className="section-kicker">Spotlight</span>
            <h2>Today's Pick</h2>
          </section>

          <div className="todays-pick">
            {/* Backdrop */}
            {todaysPick.backdrop_url
              ? <img className="todays-pick-backdrop" src={todaysPick.backdrop_url} alt="" aria-hidden="true" />
              : <div className="todays-pick-backdrop-fallback" />
            }
            <div className="todays-pick-overlay" />

            {/* Text content */}
            <div className="todays-pick-content">
              <span className="todays-pick-eyebrow">Today's Pick</span>

              {/* Meta row: rating • language • year */}
              <div className="todays-pick-meta">
                {todaysPick.rating > 0 && (
                  <span className="todays-pick-rating">
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                      <path d="M8 1l1.8 5.4H15l-4.6 3.3 1.7 5.3L8 11.8l-4.1 3.2 1.7-5.3L1 6.4h5.2L8 1Z"/>
                    </svg>
                    {todaysPick.rating}
                  </span>
                )}
                {todaysPick.language && <span className="todays-pick-badge">{todaysPick.language}</span>}
                {todaysPick.year && <span className="todays-pick-badge">{todaysPick.year}</span>}
                <span className="todays-pick-badge" style={{ textTransform: "capitalize" }}>
                  {todaysPick.content_type === "movie" ? "Movie" : "TV Show"}
                </span>
              </div>

              {/* Title */}
              <h2 className="todays-pick-title">{todaysPick.title}</h2>

              {/* Genres */}
              {todaysPick.genres?.length > 0 && (
                <div className="todays-pick-genres">
                  {todaysPick.genres.map((g) => (
                    <span key={g} className="todays-pick-genre">{g}</span>
                  ))}
                </div>
              )}

              {/* Overview */}
              {todaysPick.overview && (
                <p className="todays-pick-overview">{todaysPick.overview}</p>
              )}

              {/* Watchlist alert */}
              {todaysPick.in_watchlist && (
                <div className="todays-pick-watchlist-alert">
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M3.5 2.5h9a1 1 0 0 1 1 1v9.5L8 10.2 2.5 13V3.5a1 1 0 0 1 1-1Z"
                      stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"
                      fill="currentColor" fillOpacity="0.25"/>
                  </svg>
                  Already in your watchlist — high time you watched it!
                </div>
              )}

              {/* CTA */}
              <div className="todays-pick-actions">
                <Link
                  href={`/title/${todaysPick.content_type}/${todaysPick.tmdb_id}`}
                  className="todays-pick-btn-primary"
                >
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <path d="M4 3.5l9 4.5-9 4.5V3.5Z"/>
                  </svg>
                  View Details
                </Link>
                <Link href="/recommend" className="todays-pick-btn-secondary">
                  Not for you? Let us pick more
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </Link>
              </div>
            </div>

            {/* Poster */}
            {todaysPick.poster_url && (
              <div className="todays-pick-poster">
                <img src={todaysPick.poster_url} alt={todaysPick.title} />
              </div>
            )}
          </div>
        </>
      ) : null}

      {/* Trending */}
      {trending === undefined && <div className="poster-row-skeleton" />}
      {trending === "error" && <SectionError what="trending titles" onRetry={loadTrending} />}
      {Array.isArray(trending) && trending.length > 0 && (
        <>
          <section className="section-head">
            <span className="section-kicker">Trending Today</span>
            <h2>Top Searches Across the Globe</h2>
          </section>
          <div className="trending-poster-grid">
            {trending.slice(0, 16).map((item) => (
              <Link
                key={`${item.content_type}-${item.tmdb_id}`}
                href={`/title/${item.content_type}/${item.tmdb_id}`}
                className="trending-poster-item"
              >
                {item.poster_url ? (
                  <img src={item.poster_url} alt={item.title} />
                ) : (
                  <div style={{
                    width: "100%",
                    aspectRatio: "2 / 3",
                    background: "rgba(255,255,255,0.04)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "0.7rem",
                    color: "var(--muted)",
                    textAlign: "center",
                    padding: "0.5rem",
                  }}>
                    {item.title}
                  </div>
                )}
                <div className="trending-poster-overlay">
                  <span>{item.title}{item.year ? ` (${item.year})` : ""}</span>
                </div>
              </Link>
            ))}
          </div>
        </>
      )}

      {/* Latest OTT Releases */}
      {ott === undefined && <div className="poster-row-skeleton" />}
      {ott === "error" && <SectionError what="the latest streaming releases" onRetry={loadOtt} />}
      {ottReleases.length > 0 && (
        <>
          <section className="section-head">
            <span className="section-kicker">Latest OTT Releases</span>
            <h2>
              {ottProviders.length > 0
                ? `Recent Arrivals on ${ottProviders.slice(0, 3).join(", ")} & more`
                : "Recent Arrivals on your streaming platforms"}
            </h2>
          </section>
          <div className="ott-scroll-row">
            {ottReleases.map((item) => (
              <Link
                key={`${item.content_type}-${item.tmdb_id}`}
                href={`/title/${item.content_type}/${item.tmdb_id}`}
                className="ott-scroll-item"
              >
                {item.poster_url ? (
                  <img src={item.poster_url} alt={item.title} />
                ) : (
                  <div style={{
                    width: "100%",
                    aspectRatio: "2 / 3",
                    background: "rgba(255,255,255,0.04)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "0.7rem",
                    color: "var(--muted)",
                    textAlign: "center",
                    padding: "0.5rem",
                  }}>
                    {item.title}
                  </div>
                )}
                <div className="trending-poster-overlay">
                  <span>{item.title}{item.year ? ` (${item.year})` : ""}</span>
                </div>
              </Link>
            ))}
          </div>
        </>
      )}

    </AppShell>
  );
}
