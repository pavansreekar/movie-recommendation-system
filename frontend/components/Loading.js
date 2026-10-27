"use client";

import { useEffect, useState } from "react";

/* Loading states shared by every page: an animated NextPick mark, a status line that
   cycles through what is being fetched, and placeholders shaped like the real content.
   All motion stops for people who ask for reduced motion (see globals.css). */

// One line of status text with a moving shimmer. With several messages it moves on to
// the next every `interval` ms, so a long wait reads as progress rather than a stall.
export function StatusLine({ messages, interval = 2200, className = "" }) {
  const list = Array.isArray(messages) ? messages : [messages];
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (list.length < 2) return undefined;
    const id = setInterval(() => setIndex((i) => (i + 1) % list.length), interval);
    return () => clearInterval(id);
  }, [list.length, interval]);

  return (
    <p className={`np-status ${className}`} role="status" aria-live="polite">
      <span key={index} className="np-status-text">{list[index % list.length]}</span>
      <span className="np-status-dots" aria-hidden="true"><i /><i /><i /></span>
    </p>
  );
}

// The NextPick mark: the N draws itself stroke by stroke inside a slowly orbiting ring.
export function LoaderMark({ size = 64 }) {
  return (
    <div className="np-mark" style={{ "--np-mark-size": `${size}px` }} aria-hidden="true">
      <span className="np-mark-orbit" />
      <svg viewBox="0 0 32 32" width={size} height={size}>
        <defs>
          <linearGradient id="np-mark-bg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#C47820" />
            <stop offset="100%" stopColor="#E8A23A" />
          </linearGradient>
        </defs>
        <rect width="32" height="32" rx="8" fill="url(#np-mark-bg)" />
        <path className="np-mark-ghost" d="M24 26.5V5.5L8 26.5V5.5" />
        <path className="np-mark-pen" d="M24 26.5V5.5L8 26.5V5.5" pathLength="1" />
      </svg>
    </div>
  );
}

// Whole-page loader, shown while the page itself (or its main data) is on its way.
// `compact` fits it inside a page that already shows the navigation bar.
export function PageLoader({ messages, compact = false }) {
  return (
    <div className={`np-page-loader${compact ? " np-page-loader-compact" : ""}`}>
      {!compact && <div className="np-topbar" aria-hidden="true" />}
      <div className="np-page-loader-glow" aria-hidden="true" />
      <LoaderMark size={compact ? 52 : 64} />
      {!compact && <div className="np-page-loader-brand">NextPick</div>}
      <StatusLine messages={messages} />
    </div>
  );
}

// Poster placeholders: a grid (reusing the page's own grid class so the sizes match) or a
// single scrolling row like the streaming-releases strip.
export function PosterSkeleton({ count = 8, caption, gridClass = "trending-poster-grid", row = false }) {
  return (
    <div className="np-skel-block">
      {caption && <StatusLine messages={caption} className="np-status-sm" />}
      <div className={row ? "np-skel-row" : gridClass} aria-hidden="true">
        {Array.from({ length: count }, (_, i) => (
          <div key={i} className="np-skel np-skel-poster" style={{ animationDelay: `${i * 60}ms` }} />
        ))}
      </div>
    </div>
  );
}

// Placeholder in the shape of the Today's Pick spotlight: copy on the left, poster on the right.
export function SpotlightSkeleton({ caption }) {
  return (
    <div className="np-skel-block">
      {caption && <StatusLine messages={caption} className="np-status-sm" />}
      <div className="np-skel-spotlight" aria-hidden="true">
        <div className="np-skel-spotlight-copy">
          <span className="np-skel np-skel-line" style={{ width: "18%" }} />
          <span className="np-skel np-skel-line np-skel-line-xl" style={{ width: "52%" }} />
          <span className="np-skel-chips"><i className="np-skel" /><i className="np-skel" /><i className="np-skel" /></span>
          <span className="np-skel np-skel-line" style={{ width: "84%" }} />
          <span className="np-skel np-skel-line" style={{ width: "76%" }} />
          <span className="np-skel np-skel-line" style={{ width: "48%" }} />
          <span className="np-skel-buttons"><i className="np-skel" /><i className="np-skel" /></span>
        </div>
        <div className="np-skel np-skel-spotlight-poster" />
      </div>
    </div>
  );
}

// What a section shows when it still couldn't load after its retries.
export function SectionError({ what, onRetry }) {
  return (
    <div className="section-error">
      <span>Couldn't load {what}. The server may still be starting up.</span>
      <button type="button" className="section-error-btn" onClick={onRetry}>Try again</button>
    </div>
  );
}
