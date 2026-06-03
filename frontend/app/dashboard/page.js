"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import { apiRequest } from "../../lib/api";
import { useSessionGuard } from "../../components/useSessionGuard";
import { useRouter } from "next/navigation";

const quickLinks = [
  {
    href: "/recommend",
    label: "Get Recommendations",
    description: "Filter by genre, mood, platform and language to find your next watch.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6L12 2Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    ),
    primary: true,
  },
  {
    href: "/search",
    label: "Search Titles",
    description: "Look up any movie or TV show and add it to your watched history.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
        <path d="M16.5 16.5l4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    ),
    primary: false,
  },
  {
    href: "/history",
    label: "Watch History",
    description: "Browse everything you've marked as watched. Your history feeds the recommender.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M4 12a8 8 0 1 0 2.2-5.5" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        <path d="M4 5.5v4.5h4.5" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        <path d="M12 8v4.2l2.8 1.8" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
      </svg>
    ),
    primary: false,
  },
  {
    href: "/watchlist",
    label: "Watchlist",
    description: "Save titles you want to watch later in one place.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M7 4.5h10a1.5 1.5 0 0 1 1.5 1.5v13.4l-6.5-3.9-6.5 3.9V6A1.5 1.5 0 0 1 7 4.5Z"
          stroke="currentColor"
          strokeLinejoin="round"
          strokeWidth="1.8"
        />
      </svg>
    ),
    primary: false,
  },
];

export default function HomePage() {
  const session = useSessionGuard();
  const router = useRouter();
  const [watchedCount, setWatchedCount] = useState(null);
  const [trending, setTrending] = useState([]);
  const [ottReleases, setOttReleases] = useState([]);
  const [todaysPick, setTodaysPick] = useState(undefined); // undefined = loading, null = none

  useEffect(() => {
    if (!session.authenticated) return;
    apiRequest("/api/history")
      .then((payload) => setWatchedCount((payload.watched_items || []).length))
      .catch(() => setWatchedCount(0));
    apiRequest("/api/search/trending")
      .then((payload) => setTrending(payload.trending || []))
      .catch(() => {});
    apiRequest("/api/dashboard/ott-releases")
      .then((payload) => setOttReleases(payload.releases || []))
      .catch(() => {});
    apiRequest("/api/dashboard/today-pick")
      .then((payload) => setTodaysPick(payload.pick || null))
      .catch(() => setTodaysPick(null));
  }, [session.authenticated]);

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
          Your personal movie &amp; TV recommendation workspace. Everything is saved between sessions.
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
      ) : todaysPick ? (
        <>
          <section className="section-head">
            <span className="section-kicker">Today's Spotlight</span>
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
      {trending.length > 0 && (
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
      {ottReleases.length > 0 && (
        <>
          <section className="section-head">
            <span className="section-kicker">Latest OTT Releases</span>
            <h2>Recent Arrivals on Netflix, Prime, Hotstar &amp; more</h2>
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

      {/* Quick-action cards */}
      <section className="section-head">
        <span className="section-kicker">Quick Actions</span>
        <h2>Where do you want to go?</h2>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "1rem" }}>
        {quickLinks.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="quick-action-card premium-card"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "1rem",
              padding: "1.5rem",
              textDecoration: "none",
              borderColor: item.primary ? "rgba(124,58,237,0.32)" : "var(--line-strong)",
              transition: "transform 0.22s ease, box-shadow 0.22s ease, border-color 0.22s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = "translateY(-3px)";
              e.currentTarget.style.boxShadow = item.primary
                ? "0 20px 52px rgba(124,58,237,0.28), 0 0 0 1px rgba(124,58,237,0.28)"
                : "0 18px 44px rgba(0,0,0,0.58), 0 0 0 1px rgba(255,255,255,0.1)";
              e.currentTarget.style.borderColor = item.primary
                ? "rgba(124,58,237,0.52)"
                : "rgba(255,255,255,0.15)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = "";
              e.currentTarget.style.boxShadow = "";
              e.currentTarget.style.borderColor = item.primary ? "rgba(124,58,237,0.32)" : "var(--line-strong)";
            }}
          >
            <span style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: "2.9rem",
              height: "2.9rem",
              borderRadius: "13px",
              flexShrink: 0,
              background: item.primary ? "linear-gradient(135deg, rgba(124,58,237,0.22), rgba(79,70,229,0.18))" : "rgba(255,255,255,0.05)",
              border: `1px solid ${item.primary ? "rgba(124,58,237,0.38)" : "var(--line-strong)"}`,
              color: item.primary ? "var(--accent-strong)" : "var(--muted)",
              boxShadow: item.primary ? "0 0 20px rgba(124,58,237,0.22)" : "none",
            }}>
              {item.icon}
            </span>
            <div>
              <div style={{
                fontWeight: 700,
                fontSize: "1rem",
                color: "var(--text)",
                marginBottom: "0.3rem",
                letterSpacing: "-0.015em",
              }}>
                {item.label}
              </div>
              <div style={{ fontSize: "0.83rem", color: "var(--muted)", lineHeight: 1.58 }}>
                {item.description}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </AppShell>
  );
}
