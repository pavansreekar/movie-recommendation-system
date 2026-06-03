"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import AppShell from "../../../../components/AppShell";
import { apiRequest } from "../../../../lib/api";
import { emitWatchHistoryChange } from "../../../../lib/watchHistorySync";
import { useSessionGuard } from "../../../../components/useSessionGuard";

// ── Icons ────────────────────────────────────────────────────────────────────
function StarIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M5 3l14 9-14 9V3z" />
    </svg>
  );
}

function BookmarkIcon({ filled }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"}
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M19 12H5M12 19l-7-7 7-7" />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function SmallBookmarkIcon({ filled }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"} stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function TinySpinner() {
  return <span className="td-sim-spinner" />;
}

// ── Numbered Rating Bar ───────────────────────────────────────────────────────
function RatingBar({ value, onChange, disabled }) {
  const [hover, setHover] = useState(null);
  const display = hover ?? value;

  return (
    <div className="ratingbar-wrap">
      <div className="ratingbar-track">
        {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
          <button
            key={n}
            type="button"
            className={`ratingbar-num${n <= display ? " filled" : ""}${n === Math.round(value) && !hover ? " selected" : ""}`}
            onClick={() => !disabled && onChange(n)}
            onMouseEnter={() => !disabled && setHover(n)}
            onMouseLeave={() => setHover(null)}
            disabled={disabled}
            aria-label={`Rate ${n} out of 10`}
          >
            {n}
          </button>
        ))}
      </div>
      {value > 0 && (
        <span className="ratingbar-display">
          <StarIcon /> {value}<span className="ratingbar-max">/10</span>
        </span>
      )}
    </div>
  );
}

// ── Language code → readable name ─────────────────────────────────────────────
const LANG_NAMES = {
  te: "Telugu", hi: "Hindi", ta: "Tamil", kn: "Kannada",
  ml: "Malayalam", en: "English", ko: "Korean", ja: "Japanese",
  fr: "French", es: "Spanish", de: "German", it: "Italian",
  zh: "Chinese", ru: "Russian", pt: "Portuguese",
};

export default function TitlePage({ params }) {
  const session = useSessionGuard();
  const router = useRouter();
  const [detail, setDetail] = useState(null);
  const [rating, setRating] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [toast, setToast] = useState("");
  const [similar, setSimilar] = useState([]);
  const { contentType, tmdbId } = use(params);

  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(""), 3000);
  }

  async function loadDetail() {
    setLoading(true);
    setError("");
    try {
      const [payload, simPayload] = await Promise.all([
        apiRequest(`/api/title/${contentType}/${tmdbId}`),
        apiRequest(`/api/title/${contentType}/${tmdbId}/similar`).catch(() => ({ similar: [] })),
      ]);
      setDetail(payload);
      setRating(payload.saved_rating ?? 0);
      setSimilar(simPayload.similar || []);
    } catch (err) {
      setDetail(null);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (session.authenticated) loadDetail();
  }, [session.authenticated, contentType, tmdbId]);

  // Patch detail in-place — avoids full reload and page scroll
  function patchDetail(fields) {
    setDetail((prev) => prev ? { ...prev, ...fields } : prev);
  }

  // ── Similar card quick-action handlers ───────────────────────────────────
  const [simBusy, setSimBusy] = useState({}); // { "movie-123-watch": true }

  function patchSimilar(tmdb_id, ct, fields) {
    setSimilar((prev) => prev.map((s) =>
      s.tmdb_id === tmdb_id && s.content_type === ct ? { ...s, ...fields } : s
    ));
  }

  async function handleSimWatch(item, e) {
    e.preventDefault();
    const key = `${item.content_type}-${item.tmdb_id}-watch`;
    if (simBusy[key]) return;
    setSimBusy((p) => ({ ...p, [key]: true }));
    try {
      const payload = await apiRequest(`/api/title/${item.content_type}/${item.tmdb_id}/watch`, { method: "POST" });
      patchSimilar(item.tmdb_id, item.content_type, { is_watched: payload.is_watched });
      showToast(payload.message || (payload.is_watched ? "Added to watched" : "Removed from watched"));
    } catch { /* silent */ }
    finally { setSimBusy((p) => ({ ...p, [key]: false })); }
  }

  async function handleSimWatchlist(item, e) {
    e.preventDefault();
    const key = `${item.content_type}-${item.tmdb_id}-wl`;
    if (simBusy[key]) return;
    setSimBusy((p) => ({ ...p, [key]: true }));
    try {
      const payload = await apiRequest(`/api/title/${item.content_type}/${item.tmdb_id}/watchlist`, { method: "POST" });
      patchSimilar(item.tmdb_id, item.content_type, { in_watchlist: payload.in_watchlist });
      showToast(payload.message || (payload.in_watchlist ? "Added to watchlist" : "Removed from watchlist"));
    } catch { /* silent */ }
    finally { setSimBusy((p) => ({ ...p, [key]: false })); }
  }

  async function handleWatchToggle() {
    setActionLoading(true);
    try {
      const payload = await apiRequest(`/api/title/${contentType}/${tmdbId}/watch`, { method: "POST" });
      emitWatchHistoryChange({ tmdbId, contentType, isWatched: payload.is_watched });
      patchDetail({ is_watched: payload.is_watched });
      showToast(payload.message || (payload.is_watched ? "Added to watched history" : "Removed from watched history"));
    } catch (err) { setError(err.message); }
    finally { setActionLoading(false); }
  }

  async function handleWatchlistToggle() {
    setActionLoading(true);
    try {
      const payload = await apiRequest(`/api/title/${contentType}/${tmdbId}/watchlist`, { method: "POST" });
      patchDetail({ in_watchlist: payload.in_watchlist });
      showToast(payload.message || (payload.in_watchlist ? "Added to watchlist" : "Removed from watchlist"));
    } catch (err) { setError(err.message); }
    finally { setActionLoading(false); }
  }

  async function handleRatingSave() {
    if (!rating) return;
    setActionLoading(true);
    try {
      await apiRequest(`/api/title/${contentType}/${tmdbId}/rating`, {
        method: "POST",
        body: JSON.stringify({ rating }),
      });
      patchDetail({ saved_rating: rating });
      showToast(`Rating saved: ${rating}/10`);
    } catch (err) { setError(err.message); }
    finally { setActionLoading(false); }
  }

  async function handleRatingRemove() {
    setActionLoading(true);
    try {
      await apiRequest(`/api/title/${contentType}/${tmdbId}/rating`, { method: "DELETE" });
      setRating(0);
      patchDetail({ saved_rating: null });
      showToast("Rating removed");
    } catch (err) { setError(err.message); }
    finally { setActionLoading(false); }
  }

  if (session.loading || !session.authenticated || loading) {
    return (
      <div className="loading-screen">
        <div className="loading-pulse">Loading title…</div>
      </div>
    );
  }

  if (!detail) {
    return (
      <AppShell user={session.user}>
        <section className="td-error-wrap premium-card">
          <h2>Couldn't load this title</h2>
          <p className="td-error-msg">{error || "Something went wrong."}</p>
          <div className="td-error-actions">
            <button className="button button-primary" onClick={loadDetail}>Try again</button>
            <button className="button button-secondary" onClick={() => router.back()}>Go back</button>
          </div>
        </section>
      </AppShell>
    );
  }

  const m = detail.movie;
  const langName = LANG_NAMES[m.language] || m.language?.toUpperCase() || "N/A";
  const hasRating = detail.saved_rating != null;
  const ratingChanged = rating !== (detail.saved_rating ?? 0);

  return (
    <AppShell user={session.user}>
      {/* ── Toast notification ── */}
      {toast && <div className="td-toast">{toast}</div>}

      <div className="td-page">
        {/* ── Cinematic backdrop ── */}
        <div className="td-backdrop-wrap">
          {m.backdrop_url
            ? <img src={m.backdrop_url} alt="" className="td-backdrop-img" />
            : <div className="td-backdrop-placeholder" />
          }
          <div className="td-backdrop-gradient" />
        </div>

        {/* ── Main content card ── */}
        <div className="td-main-card">
          <div className="td-grid">
            {/* Poster */}
            <div className="td-poster-col">
              {m.poster_url
                ? <img src={m.poster_url} alt={m.title} className="td-poster" />
                : <div className="td-poster-fallback"><span>No Poster</span></div>
              }

              {/* Quick stats under poster */}
              <div className="td-poster-stats">
                <div className="td-stat">
                  <span className="td-stat-label">TMDB</span>
                  <span className="td-stat-value"><StarIcon /> {detail.original_rating?.toFixed(1)}</span>
                </div>
                <div className="td-stat-divider" />
                <div className="td-stat">
                  <span className="td-stat-label">Language</span>
                  <span className="td-stat-value"><GlobeIcon /> {langName}</span>
                </div>
                {m.year ? (
                  <>
                    <div className="td-stat-divider" />
                    <div className="td-stat">
                      <span className="td-stat-label">Year</span>
                      <span className="td-stat-value">{m.year}</span>
                    </div>
                  </>
                ) : null}
              </div>
            </div>

            {/* Info column */}
            <div className="td-info-col">
              {/* Badge + title */}
              <span className="eyebrow">{m.content_label}</span>
              <h1 className="td-title">{m.title}</h1>

              {/* Genre chips */}
              {detail.genres?.length > 0 && (
                <div className="td-genres">
                  {detail.genres.map((g) => <span key={g} className="td-genre-chip">{g}</span>)}
                </div>
              )}

              {/* Overview */}
              <p className="td-overview">{m.overview || "No description available."}</p>

              {/* Providers */}
              <div className="td-providers-row">
                <span className="td-providers-label">Available on</span>
                {detail.providers?.length
                  ? detail.providers.map((p) => <span key={p} className="td-provider-badge">{p}</span>)
                  : <span className="td-no-provider">No streaming info for {detail.region}</span>
                }
              </div>

              {/* Divider */}
              <div className="td-divider" />

              {/* Rating section */}
              <div className="td-rating-section">
                <div className="td-rating-header">
                  <span className="td-section-label">Your Rating</span>
                  {hasRating && (
                    <span className="td-saved-badge">
                      <CheckIcon /> Saved: {detail.saved_rating}/10
                    </span>
                  )}
                </div>
                <RatingBar value={rating} onChange={setRating} disabled={actionLoading} />
                <div className="td-rating-actions">
                  {ratingChanged && rating > 0 && (
                    <button className="button button-primary td-btn-sm" onClick={handleRatingSave} disabled={actionLoading}>
                      Save rating
                    </button>
                  )}
                  {hasRating && (
                    <button className="button button-ghost td-btn-sm" onClick={handleRatingRemove} disabled={actionLoading}>
                      Remove rating
                    </button>
                  )}
                </div>
              </div>

              {/* Divider */}
              <div className="td-divider" />

              {/* Action buttons */}
              <div className="td-actions">
                <button
                  className={`button td-action-btn ${detail.in_watchlist ? "button-secondary td-active" : "button-primary"}`}
                  onClick={handleWatchlistToggle}
                  disabled={actionLoading}
                >
                  <BookmarkIcon filled={detail.in_watchlist} />
                  {detail.in_watchlist ? "Remove from Watchlist" : "Add to Watchlist"}
                </button>

                <button
                  className={`button td-action-btn ${detail.is_watched ? "button-secondary td-watched-active" : "button-outline"}`}
                  onClick={handleWatchToggle}
                  disabled={actionLoading}
                >
                  <CheckIcon />
                  {detail.is_watched ? "Watched ✓" : "Mark as Watched"}
                </button>

                <button className="button button-ghost td-back-btn" onClick={() => router.back()} disabled={actionLoading}>
                  <BackIcon /> Back
                </button>
              </div>

              {error && <p className="td-error-inline">{error}</p>}
            </div>
          </div>
        </div>

        {/* ── Cast + Details cards ── */}
        <div className="td-bottom-grid">
          {/* Cast */}
          <div className="td-bottom-card premium-card">
            <span className="section-kicker">Cast</span>
            <h2 className="td-bottom-heading">Primary Cast</h2>
            {detail.cast?.length ? (
              <div className="td-cast-list">
                {detail.cast.map((name, i) => (
                  <span key={i} className="td-cast-name">{name}</span>
                ))}
              </div>
            ) : (
              <p className="td-empty-note">No cast information available.</p>
            )}
          </div>

          {/* Details */}
          <div className="td-bottom-card premium-card">
            <span className="section-kicker">Details</span>
            <h2 className="td-bottom-heading">Title Info</h2>
            <dl className="td-detail-list">
              <div className="td-detail-row">
                <dt>Language</dt>
                <dd>{langName}</dd>
              </div>
              <div className="td-detail-row">
                <dt>TMDB Rating</dt>
                <dd>★ {detail.original_rating?.toFixed(1)} / 10</dd>
              </div>
              <div className="td-detail-row">
                <dt>Your Rating</dt>
                <dd>{hasRating ? `${detail.saved_rating} / 10` : "Not rated yet"}</dd>
              </div>
              <div className="td-detail-row">
                <dt>Watched</dt>
                <dd>{detail.is_watched ? "Yes ✓" : "No"}</dd>
              </div>
              <div className="td-detail-row">
                <dt>In Watchlist</dt>
                <dd>{detail.in_watchlist ? "Yes ✓" : "No"}</dd>
              </div>
              {m.year && (
                <div className="td-detail-row">
                  <dt>Year</dt>
                  <dd>{m.year}</dd>
                </div>
              )}
            </dl>
          </div>
        </div>

        {/* ── Similar titles slider ── */}
        {similar.length > 0 && (
          <div className="td-similar-section">
            <div className="td-similar-header">
              <span className="section-kicker">You might also like</span>
              <h2 className="td-similar-heading">Similar to {m.title}</h2>
            </div>
            <div className="td-similar-scroll">
              {similar.map((item) => {
                const statusClass = item.is_watched
                  ? "td-similar-card--watched"
                  : item.in_watchlist
                  ? "td-similar-card--watchlisted"
                  : "";
                return (
                  <Link
                    key={`${item.content_type}-${item.tmdb_id}`}
                    href={`/title/${item.content_type}/${item.tmdb_id}`}
                    className={`td-similar-card ${statusClass}`}
                  >
                    {item.poster_url ? (
                      <img src={item.poster_url} alt={item.title} className="td-similar-poster" />
                    ) : (
                      <div className="td-similar-poster td-similar-poster-fallback">
                        <span>{item.title?.[0]}</span>
                      </div>
                    )}

                    {/* Diagonal corner triangle — top-left */}
                    {item.is_watched && (
                      <div className="td-similar-strip td-similar-strip--watched" />
                    )}
                    {!item.is_watched && item.in_watchlist && (
                      <div className="td-similar-strip td-similar-strip--watchlist" />
                    )}

                    <div className="td-similar-overlay">
                      <span className="td-similar-title">{item.title}</span>
                      <span className="td-similar-meta">
                        {item.content_label}
                        {item.year ? ` · ${item.year}` : ""}
                        {item.rating ? ` · ★ ${item.rating.toFixed(1)}` : ""}
                      </span>
                      {item.genres?.length > 0 && (
                        <span className="td-similar-genres">{item.genres.slice(0, 2).join(", ")}</span>
                      )}
                    </div>

                    {/* Quick-action buttons (appear on hover, same as recommend page) */}
                    <div className="td-sim-actions">
                      <button
                        className={`td-sim-btn${item.is_watched ? " td-sim-btn--active" : ""}`}
                        onClick={(e) => handleSimWatch(item, e)}
                        title={item.is_watched ? "Remove from watched" : "Mark as watched"}
                        type="button"
                      >
                        {simBusy[`${item.content_type}-${item.tmdb_id}-watch`]
                          ? <TinySpinner /> : <EyeIcon />}
                      </button>
                      <button
                        className={`td-sim-btn${item.in_watchlist ? " td-sim-btn--active" : ""}`}
                        onClick={(e) => handleSimWatchlist(item, e)}
                        title={item.in_watchlist ? "Remove from watchlist" : "Add to watchlist"}
                        type="button"
                      >
                        {simBusy[`${item.content_type}-${item.tmdb_id}-wl`]
                          ? <TinySpinner /> : <SmallBookmarkIcon filled={item.in_watchlist} />}
                      </button>
                    </div>
                  </Link>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
