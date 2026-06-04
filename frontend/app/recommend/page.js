"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import { apiRequest } from "../../lib/api";
import { emitWatchHistoryChange } from "../../lib/watchHistorySync";
import { useSessionGuard } from "../../components/useSessionGuard";

const DISPLAY_COUNT    = 16;
const FALLBACK_DISPLAY = 8;
const defaultFilters   = { content_type: "", genres: [], platforms: [], languages: [] };

// ── Step definitions ──────────────────────────────────────────────────────────

const STEP_META = [
  { key: "content_type", title: "Movie or Show?",   subtitle: "What format are you in the mood for?",      theme: "Your Format",   grid: "lg" },
  { key: "genre",        title: "Pick a Genre",      subtitle: "What kind of story speaks to you tonight? Select one or more.", theme: "Your Taste",    grid: "md", multiSelect: true },
  { key: "platform",     title: "Choose a Platform", subtitle: "Where do you like to watch? Select one or more.", theme: "Your Platform", grid: "sm", multiSelect: true },
  { key: "language",     title: "Select Language",   subtitle: "Any language preference? Pick one or more.", theme: "Your Language", grid: "md", multiSelect: true },
];
const STEPS_COUNT = STEP_META.length;

// Top platforms popular in India — shown regardless of TMDB provider availability
const TOP_INDIA_PLATFORMS = [
  "Netflix",
  "Amazon Prime Video",
  "JioHotstar",
  "Disney+",
  "Zee5",
  "SonyLIV",
  "Sun NXT",
  "Aha",
  "Apple TV",
  "YouTube",
  "FanCode",
];

// Curated genre list — maps directly to TMDB genre names
const CURATED_GENRES = [
  "Action", "Adventure", "Comedy", "Drama",
  "Horror", "Science Fiction", "Fantasy", "Romance",
  "Mystery", "Thriller", "Western",
];

// Top 7 languages shown by default
const TOP_LANGUAGES = ["English", "Hindi", "Telugu", "Tamil", "Kannada", "Malayalam", "Korean"];

// ── Icon / script maps ────────────────────────────────────────────────────────

const ICONS = {
  content_type: { "": "ti-adjustments-horizontal", movie: "ti-movie", tv: "ti-device-tv" },
  genre: {
    "": "ti-sparkles",
    "Action": "ti-flame", "Action & Adventure": "ti-sword", "Adventure": "ti-map-2",
    "Animation": "ti-sparkles", "Comedy": "ti-mood-happy", "Crime": "ti-badge-detective",
    "Documentary": "ti-camera", "Drama": "ti-masks-theater", "Family": "ti-users",
    "Fantasy": "ti-wand", "History": "ti-books", "Horror": "ti-ghost",
    "Kids": "ti-candy", "Music": "ti-music", "Mystery": "ti-eye",
    "Reality": "ti-device-tv", "Romance": "ti-heart", "Sci-Fi & Fantasy": "ti-rocket",
    "Science Fiction": "ti-robot", "Thriller": "ti-eye-closed",
    "War": "ti-sword", "War & Politics": "ti-world", "Western": "ti-horse",
    "Talk": "ti-microphone", "TV Movie": "ti-movie", "Soap": "ti-bubbles",
    "Biography": "ti-book",
  },
  platform: {
    "": "ti-device-mobile",
    "Netflix": "ti-brand-netflix",
    "Amazon Prime Video": "ti-brand-amazon",
    "JioHotstar": "ti-stars",
    "Disney+": "ti-wand",
    "YouTube": "ti-brand-youtube",
    "Apple TV": "ti-brand-apple",
    "Zee5": "ti-device-tv",
    "SonyLIV": "ti-device-tv-old",
    "Sun NXT": "ti-sun",
    "Aha": "ti-leaf",
    "FanCode": "ti-device-gamepad",
  },
  language: { "": "ti-world" },
};

const LANG_SCRIPTS = {
  Telugu: "తె", Hindi: "हिं", English: "En", Tamil: "த", Kannada: "ಕ",
  Malayalam: "മ", Marathi: "मर", Bengali: "বাং", Punjabi: "ਪੰ", Gujarati: "ગુ",
  Urdu: "اردو", Japanese: "日", Korean: "한", Chinese: "中", French: "Fr",
  Spanish: "Es", German: "De", Italian: "It", Portuguese: "Pt",
  Russian: "Рус", Thai: "ไท", Arabic: "عر", Turkish: "Tr",
};

function getIcon(stepKey, value) {
  return ICONS[stepKey]?.[value] ?? "ti-sparkles";
}

// ── Framer-motion step variants ───────────────────────────────────────────────

const stepVariants = {
  enter:  (dir) => ({ x: dir *  52, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit:   (dir) => ({ x: dir * -52, opacity: 0 }),
};

// ── WizardCard ────────────────────────────────────────────────────────────────

function WizardCard({ stepKey, value, label, isSelected, isAny, onSelect, size }) {
  const icon   = getIcon(stepKey, value);
  const script = stepKey === "language" && value ? LANG_SCRIPTS[label] : null;

  const cls = [
    "wiz-card",
    `wiz-card-${size}`,
    isSelected ? "wiz-selected" : "",
    isAny      ? "wiz-any"      : "",
  ].filter(Boolean).join(" ");

  return (
    <motion.button
      type="button"
      className={cls}
      onClick={onSelect}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      whileHover={size !== "sm" ? { y: -2, transition: { duration: 0.18, ease: "easeOut" } } : undefined}
      whileTap={{ scale: 0.97, transition: { duration: 0.08 } }}
    >
      {script
        ? <span className="wiz-script">{script}</span>
        : <i className={`ti ${icon} wiz-icon`} aria-hidden="true" />
      }
      <span className="wiz-label">{label}</span>
    </motion.button>
  );
}

// ── WizardStep ────────────────────────────────────────────────────────────────

function WizardStep({ step, stepIndex, filters, options, onSelect, onNext, onBack, canContinue, isLast, direction, showAllLanguages, onToggleLanguages }) {
  const gridClass = `wiz-grid-${step.grid}`;

  function computeIsSelected(opt) {
    if (step.key === "platform") {
      const platforms = filters.platforms || [];
      return opt.value === "" ? platforms.length === 0 : platforms.includes(opt.value);
    }
    if (step.key === "genre") {
      const genres = filters.genres || [];
      return opt.value === "" ? genres.length === 0 : genres.includes(opt.value);
    }
    if (step.key === "language") {
      const langs = filters.languages || [];
      return opt.value === "" ? langs.length === 0 : langs.includes(opt.value);
    }
    return filters[step.key] === opt.value;
  }

  return (
    <motion.div
      key={stepIndex}
      custom={direction}
      variants={stepVariants}
      initial="enter"
      animate="center"
      exit="exit"
      transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="wiz-step-inner">
        <div className="wiz-step-meta">
          <span className="wiz-step-pill">Step {stepIndex + 1} of {STEPS_COUNT}</span>
          <span className="wiz-step-theme">{step.theme}</span>
        </div>
        <h2 className="wiz-step-title">{step.title}</h2>
        <p className="wiz-step-subtitle">{step.subtitle}</p>

        <div className={gridClass}>
          {options.map((opt, i) => (
            <motion.div
              key={opt.value || "__any__"}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.24, delay: Math.min(i * 0.02, 0.4), ease: [0.16, 1, 0.3, 1] }}
            >
              <WizardCard
                stepKey={step.key}
                value={opt.value}
                label={opt.label}
                isSelected={computeIsSelected(opt)}
                isAny={opt.value === ""}
                onSelect={() => onSelect(step.key, opt.value)}
                size={step.grid}
              />
            </motion.div>
          ))}
        </div>

        {step.key === "language" && (
          <button className="lang-expand-btn" type="button" onClick={onToggleLanguages}>
            <i className={`ti ${showAllLanguages ? "ti-chevron-up" : "ti-chevron-down"}`} aria-hidden="true" />
            {showAllLanguages ? "Show fewer languages" : "View more languages"}
          </button>
        )}

        <div className="wiz-actions">
          {stepIndex > 0 && (
            <button className="wiz-btn-back" type="button" onClick={onBack} aria-label="Go back">
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M10 3L5 8L10 13" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          )}
          <button className="wiz-btn-continue" type="button" onClick={onNext} disabled={!canContinue}>
            {isLast ? "Find me a watch" : "Continue"}
            <i className="ti ti-arrow-right wiz-arrow" aria-hidden="true" />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

// ── RecPosterGrid ─────────────────────────────────────────────────────────────

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.6" />
      <path d="M5 8l2 2 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function EyeIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M1.5 8s2.5-5 6.5-5 6.5 5 6.5 5-2.5 5-6.5 5-6.5-5-6.5-5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
function BookmarkIcon({ filled }) {
  return (
    <svg viewBox="0 0 16 16" fill={filled ? "currentColor" : "none"} aria-hidden="true">
      <path d="M4.5 2.5h7a1 1 0 0 1 1 1v9.5l-4.5-2.7-4.5 2.7V3.5a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
function TinySpinner() {
  return <span style={{ display:"inline-block", width:"0.72rem", height:"0.72rem", borderRadius:"50%", border:"1.5px solid rgba(255,255,255,0.28)", borderTopColor:"#fff", animation:"spin 0.7s linear infinite", flexShrink:0 }} />;
}

function RecPosterGrid({ recs, watchingKeys, exitingKeys, watchlistingKeys, watchlistedKeys, onWatch, onWatchlist }) {
  return (
    <div className="rec-poster-grid">
      {recs.map((rec) => {
        const key = `${rec.movie.content_type}-${rec.movie.tmdb_id}`;
        const isWatching     = watchingKeys.has(key);
        const isExiting      = exitingKeys.has(key);
        const isWatchlisting = watchlistingKeys.has(key);
        const isWatchlisted  = watchlistedKeys.has(key);
        return (
          <div className={`rec-poster-card${isExiting ? " rec-poster-exit" : ""}`} key={key}>
            <Link href={`/title/${rec.movie.content_type}/${rec.movie.tmdb_id}`} className="rec-poster-link">
              {rec.movie.poster_url ? <img src={rec.movie.poster_url} alt={rec.movie.title} /> : <div className="poster-fallback">{rec.movie.title}</div>}
            </Link>
            <div className="rec-poster-overlay"><span className="rec-poster-title">{rec.movie.title}</span></div>
            <div className="rec-poster-actions">
              <button className="rec-poster-btn" onClick={() => onWatch(rec.movie)} disabled={isWatching || isExiting} title="Mark as watched">
                {isWatching ? <TinySpinner /> : <EyeIcon />}
              </button>
              <button className={`rec-poster-btn${isWatchlisted ? " saved" : ""}`} onClick={() => onWatchlist(rec.movie)} disabled={isWatchlisting || isWatchlisted} title={isWatchlisted ? "Saved" : "Save to watchlist"}>
                {isWatchlisting ? <TinySpinner /> : <BookmarkIcon filled={isWatchlisted} />}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function RecommendPage() {
  const session = useSessionGuard();

  // Wizard
  const [currentStep, setCurrentStep]         = useState(0);
  const [direction, setDirection]             = useState(1);
  const [touchedSteps, setTouchedSteps]       = useState(new Set());
  const [wizardDone, setWizardDone]           = useState(false);
  const [filters, setFilters]                 = useState(defaultFilters);
  const [showAllLanguages, setShowAllLanguages] = useState(false);

  // Recs
  const [data, setData]                         = useState(null);
  const [loading, setLoading]                   = useState(true);
  const [recommending, setRecommending]         = useState(false);
  const [error, setError]                       = useState("");
  const [displayedRecs, setDisplayedRecs]       = useState([]);
  const [reserveRecs, setReserveRecs]           = useState([]);
  const [watchingKeys, setWatchingKeys]         = useState(new Set());
  const [exitingKeys, setExitingKeys]           = useState(new Set());
  const [watchlistingKeys, setWatchlistingKeys] = useState(new Set());
  const [watchlistedKeys, setWatchlistedKeys]   = useState(new Set());
  const [toasts, setToasts]                     = useState([]);
  const toastTimers                             = useRef({});

  // Fallback section states — each has a displayed list + reserve for replace-on-watch
  const [fbPlatDisplayed, setFbPlatDisplayed] = useState([]);
  const [fbPlatReserve,   setFbPlatReserve]   = useState([]);
  const [fbGenDisplayed,  setFbGenDisplayed]  = useState([]);
  const [fbGenReserve,    setFbGenReserve]    = useState([]);
  const [fbLangDisplayed, setFbLangDisplayed] = useState([]);
  const [fbLangReserve,   setFbLangReserve]   = useState([]);

  // "Show me more" — tracks whether user has clicked the button yet
  const [showMoreClicked, setShowMoreClicked] = useState(false);

  // Build step option arrays once data loads (recomputes when showAllLanguages changes)
  const stepOptions = useMemo(() => {
    if (!data) return [];
    return STEP_META.map((m) => {
      const availablePlatforms = TOP_INDIA_PLATFORMS;
      const visibleLanguages = showAllLanguages
        ? (data.language_options || [])
        : TOP_LANGUAGES.filter(l => (data.language_options || []).includes(l));

      const dynMap = {
        content_type: [{ value: "movie", label: "Movie" }, { value: "tv", label: "TV Show" }],
        genre:        CURATED_GENRES.map((o) => ({ value: o, label: o })),
        platform:     availablePlatforms.map((o) => ({ value: o, label: o })),
        language:     visibleLanguages.map((o) => ({ value: o, label: o })),
      };
      return [{ value: "", label: "Any" }, ...(dynMap[m.key] || [])];
    });
  }, [data, showAllLanguages]);

  // Platform and genre are multi-select where empty = "Any", always continuable
  const currentStepMeta = STEP_META[currentStep];
  const OPTIONAL_STEPS = new Set(["platform", "genre", "language"]);
  const canContinue = OPTIONAL_STEPS.has(currentStepMeta?.key) || touchedSteps.has(currentStep);

  function handleCardSelect(stepKey, value) {
    const MULTI_SELECT_MAP = { platform: "platforms", genre: "genres", language: "languages" };
    if (stepKey in MULTI_SELECT_MAP) {
      const arrKey = MULTI_SELECT_MAP[stepKey];
      setFilters((prev) => {
        if (value === "") return { ...prev, [arrKey]: [] };
        const current = prev[arrKey] || [];
        const exists = current.includes(value);
        return { ...prev, [arrKey]: exists ? current.filter(v => v !== value) : [...current, value] };
      });
    } else {
      setFilters((prev) => ({ ...prev, [stepKey]: value }));
    }
    setTouchedSteps((prev) => new Set([...prev, currentStep]));
  }

  function goNext() {
    if (currentStep < STEPS_COUNT - 1) {
      setDirection(1);
      setCurrentStep((c) => c + 1);
    } else {
      setWizardDone(true);
      doRecommend(filters);
    }
  }

  function goBack() {
    if (currentStep > 0) {
      setDirection(-1);
      setCurrentStep((c) => c - 1);
    }
  }

  function goToStep(i) {
    if (i < currentStep || touchedSteps.has(i)) {
      setDirection(i < currentStep ? -1 : 1);
      setCurrentStep(i);
    }
  }

  function reconfigure() {
    setWizardDone(false);
    setCurrentStep(0);
    setDirection(1);
  }

  // Toast helpers
  function addToast(message) {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, exiting: false }]);
    toastTimers.current[id] = setTimeout(() => dismissToast(id), 3000);
  }
  function dismissToast(id) {
    clearTimeout(toastTimers.current[id]);
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 260);
  }

  function applyPayload(payload) {
    const all = payload.recommendations || [];
    setDisplayedRecs(all.slice(0, DISPLAY_COUNT));
    setReserveRecs(all.slice(DISPLAY_COUNT));
    setShowMoreClicked(false); // reset on every fresh payload

    const splitFallback = (arr) => [arr.slice(0, FALLBACK_DISPLAY), arr.slice(FALLBACK_DISPLAY)];
    const [p0, p1] = splitFallback(payload.fallback_other_platforms || []);
    const [g0, g1] = splitFallback(payload.fallback_other_genres    || []);
    const [l0, l1] = splitFallback(payload.fallback_other_languages || []);
    setFbPlatDisplayed(p0); setFbPlatReserve(p1);
    setFbGenDisplayed(g0);  setFbGenReserve(g1);
    setFbLangDisplayed(l0); setFbLangReserve(l1);

    setData(payload);
  }

  async function loadDashboard() {
    setLoading(true);
    setError("");
    try {
      applyPayload(await apiRequest("/api/dashboard"));
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  async function doRecommend(filtersToUse) {
    setRecommending(true);
    setError("");
    try {
      const payload = {
        content_type: filtersToUse.content_type,
        genres: filtersToUse.genres,
        platforms: filtersToUse.platforms,
        languages: filtersToUse.languages,
      };
      applyPayload(await apiRequest("/api/dashboard/recommend", { method: "POST", body: JSON.stringify(payload) }));
      setWatchlistedKeys(new Set());
    } catch (err) {
      setError(err.message);
    } finally {
      setRecommending(false);
    }
  }

  useEffect(() => { if (session.authenticated) loadDashboard(); }, [session.authenticated]);
  useEffect(() => { if (!session.loading && !session.authenticated) setLoading(false); }, [session.loading, session.authenticated]);

  function removeKeyFromAllLists(key) {
    // ── Main recommendations ──────────────────────────────────────────────────
    setDisplayedRecs((prev) => {
      const filtered = prev.filter((r) => `${r.movie.content_type}-${r.movie.tmdb_id}` !== key);
      setReserveRecs((pool) => {
        if (pool.length > 0 && filtered.length < prev.length) {
          const [next, ...rest] = pool;
          setDisplayedRecs([...filtered, next]);
          return rest;
        }
        return pool;
      });
      return filtered;
    });

    // ── Helper: remove from a fallback displayed list, promote from its reserve ─
    const removeAndPromote = (setDisplayed, setReserve) => {
      setDisplayed((prev) => {
        const filtered = prev.filter((r) => `${r.movie.content_type}-${r.movie.tmdb_id}` !== key);
        if (filtered.length < prev.length) {
          setReserve((pool) => {
            if (pool.length > 0) {
              const [next, ...rest] = pool;
              setDisplayed([...filtered, next]);
              return rest;
            }
            return pool;
          });
        }
        return filtered;
      });
    };

    removeAndPromote(setFbPlatDisplayed, setFbPlatReserve);
    removeAndPromote(setFbGenDisplayed,  setFbGenReserve);
    removeAndPromote(setFbLangDisplayed, setFbLangReserve);
  }

  async function toggleWatch(movie) {
    const key = `${movie.content_type}-${movie.tmdb_id}`;
    if (watchingKeys.has(key) || exitingKeys.has(key)) return;
    setWatchingKeys((p) => new Set([...p, key]));
    try {
      const payload = await apiRequest(`/api/title/${movie.content_type}/${movie.tmdb_id}/watch`, { method: "POST" });
      emitWatchHistoryChange({ tmdbId: movie.tmdb_id, contentType: movie.content_type, isWatched: payload.is_watched });
      setExitingKeys((p) => new Set([...p, key]));
      addToast(`"${movie.title}" added to watched history`);
      setTimeout(() => {
        removeKeyFromAllLists(key);
        setExitingKeys((p) => { const n = new Set(p); n.delete(key); return n; });
      }, 380);
    } catch (err) { setError(err.message); }
    finally { setWatchingKeys((p) => { const n = new Set(p); n.delete(key); return n; }); }
  }

  async function toggleWatchlist(movie) {
    const key = `${movie.content_type}-${movie.tmdb_id}`;
    if (watchlistingKeys.has(key) || watchlistedKeys.has(key)) return;
    setWatchlistingKeys((p) => new Set([...p, key]));
    try {
      await apiRequest(`/api/title/${movie.content_type}/${movie.tmdb_id}/watchlist`, { method: "POST" });
      setWatchlistedKeys((p) => new Set([...p, key]));
      addToast(`"${movie.title}" saved to watchlist`);
    } catch (err) { setError(err.message); }
    finally { setWatchlistingKeys((p) => { const n = new Set(p); n.delete(key); return n; }); }
  }

  function handleShowMore() {
    // Reveal up to 8 additional titles from reserve, then expose the fallback sections
    setDisplayedRecs((prev) => {
      const extras = reserveRecs.slice(0, 8);
      setReserveRecs(reserveRecs.slice(8));
      return [...prev, ...extras];
    });
    setShowMoreClicked(true);
  }

  const sharedGridProps = { watchingKeys, exitingKeys, watchlistingKeys, watchlistedKeys, onWatch: toggleWatch, onWatchlist: toggleWatchlist };

  if (session.loading || (session.authenticated && loading)) return <div className="loading-screen">Loading...</div>;
  if (!session.authenticated) return <div className="loading-screen">Redirecting...</div>;
  if (!data) {
    return (
      <AppShell user={session.user}>
        <section className="hero premium-card compact-hero">
          <span className="section-kicker">Recommend</span>
          <h1 style={{ margin: "0.55rem 0 1rem", fontSize: "2.3rem", lineHeight: 0.98, letterSpacing: "-0.05em" }}>
            Couldn&apos;t load recommendations
          </h1>
          <p>{error || "Something went wrong."}</p>
          <div className="action-row"><button className="button button-primary" onClick={loadDashboard}>Try again</button></div>
        </section>
      </AppShell>
    );
  }

  const hasMainRecs      = displayedRecs.length > 0;
  const filtersActive    = data.filters?.active;
  const totalMainRecs    = (data.recommendations || []).length;
  // "enough" = 16+ results → show button, hide fallbacks until clicked
  // "not enough" = <16 → show fallbacks immediately (existing behaviour)
  const enoughForShowMore = totalMainRecs >= DISPLAY_COUNT;
  // Use live state (not raw data) so remove+promote works immediately
  const fbPlatforms   = fbPlatDisplayed;
  const fbGenres      = fbGenDisplayed;
  const fbLanguages   = fbLangDisplayed;
  const hasFallbacks  = fbPlatforms.length > 0 || fbGenres.length > 0 || fbLanguages.length > 0;
  // Fallbacks visible: either not enough results (auto-show), or user clicked "Show me more"
  const showFallbacks = !recommending && hasFallbacks && (
    (!enoughForShowMore && filtersActive) || showMoreClicked
  );
  // Button visible: wizard done, main recs exist, enough total results, not yet clicked
  const showShowMoreBtn = wizardDone && hasMainRecs && !recommending && enoughForShowMore && !showMoreClicked;

  // Summary labels for compact view
  const summaryTags = STEP_META.map((m) => {
    if (m.key === "platform") {
      const plats = filters.platforms || [];
      return { key: "platform", label: plats.length === 0 ? "Any" : plats.join(", ") };
    }
    if (m.key === "genre") {
      const genres = filters.genres || [];
      return { key: "genre", label: genres.length === 0 ? "Any" : genres.join(", ") };
    }
    if (m.key === "language") {
      const langs = filters.languages || [];
      return { key: "language", label: langs.length === 0 ? "Any" : langs.join(", ") };
    }
    const val = filters[m.key];
    if (!val) return { key: m.key, label: "Any" };
    const opts = stepOptions[STEP_META.indexOf(m)] || [];
    return { key: m.key, label: opts.find((o) => o.value === val)?.label || val };
  });

  return (
    <AppShell user={session.user}>
      {error                ? <div className="alert alert-warning">{error}</div>                        : null}
      {data.metadata_error  ? <div className="alert alert-warning">{data.metadata_error}</div>          : null}

      {/* ── Configurator ── */}
      <section className="section-head">
        <span className="section-kicker">Configure</span>
        <h2>Dial in your next watch</h2>
      </section>

      <div className="premium-card" style={{ overflow: "hidden" }}>
        {wizardDone ? (
          /* Compact summary after wizard is done */
          <div className="wiz-summary">
            <span className="wiz-summary-label">Filters</span>
            {summaryTags.map((t) => (
              <span key={t.key} className="wiz-summary-tag">{t.label}</span>
            ))}
            <button className="wiz-reconfig" type="button" onClick={reconfigure}>
              <i className="ti ti-adjustments-horizontal" style={{ marginRight: 5, verticalAlign: "-2px" }} />
              Reconfigure
            </button>
          </div>
        ) : (
          /* Active wizard */
          <div className="wiz-panel">
            {/* Progress bar */}
            <div className="wiz-progress" role="progressbar" aria-valuenow={currentStep + 1} aria-valuemax={STEPS_COUNT}>
              {STEP_META.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  className={`wiz-dot${i < currentStep ? " done" : i === currentStep ? " active" : ""}`}
                  onClick={() => goToStep(i)}
                  aria-label={`Step ${i + 1}`}
                  style={{ border: "none", padding: 0, cursor: i < currentStep ? "pointer" : "default" }}
                />
              ))}
            </div>

            {/* Animated step */}
            <AnimatePresence mode="wait" custom={direction}>
              {stepOptions.length > 0 && (
                <WizardStep
                  key={currentStep}
                  step={STEP_META[currentStep]}
                  stepIndex={currentStep}
                  filters={filters}
                  options={stepOptions[currentStep] || []}
                  onSelect={handleCardSelect}
                  onNext={goNext}
                  onBack={goBack}
                  canContinue={canContinue}
                  isLast={currentStep === STEPS_COUNT - 1}
                  direction={direction}
                  showAllLanguages={showAllLanguages}
                  onToggleLanguages={() => setShowAllLanguages((v) => !v)}
                />
              )}
            </AnimatePresence>
          </div>
        )}
      </div>

      {/* ── Recommendations — only shown after wizard is completed ── */}
      {wizardDone && (
        <>
          <section className="section-head">
            <span className="section-kicker">Recommendations</span>
            <h2>You might like to watch these</h2>
          </section>

          {data.recommendations_error ? <div className="alert alert-warning">{data.recommendations_error}</div> : null}

          {recommending ? (
            <div className="wiz-rec-loading premium-card">
              <div className="wiz-rec-spinner" />
              <div className="wiz-rec-loading-title">Finding your matches…</div>
              <div className="wiz-rec-loading-sub">
                Searching across {summaryTags.map(t => t.label).filter(l => l !== "Any").join(", ") || "all titles"}
              </div>
            </div>
          ) : (
            <>
              {!data.recommendations_error && !hasMainRecs ? (
                <div className="empty-state premium-card">
                  No titles found for your filters. Try adjusting your selections.
                </div>
              ) : null}

              {hasMainRecs ? <RecPosterGrid recs={displayedRecs} {...sharedGridProps} /> : null}

              {/* ── "Show me more" button — only when enough results exist ── */}
              {showShowMoreBtn && (
                <div style={{ display: "flex", justifyContent: "center", marginTop: "1.5rem" }}>
                  <button
                    type="button"
                    onClick={handleShowMore}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "0.5rem",
                      padding: "0.75rem 2rem",
                      borderRadius: "12px",
                      border: "1px solid rgba(255,255,255,0.12)",
                      background: "rgba(255,255,255,0.06)",
                      color: "var(--text)",
                      fontSize: "0.9rem",
                      fontWeight: 600,
                      cursor: "pointer",
                      letterSpacing: "-0.01em",
                      transition: "background 0.18s, border-color 0.18s",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "rgba(255,255,255,0.1)";
                      e.currentTarget.style.borderColor = "rgba(255,255,255,0.22)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "rgba(255,255,255,0.06)";
                      e.currentTarget.style.borderColor = "rgba(255,255,255,0.12)";
                    }}
                  >
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path d="M8 3v10M3 8l5 5 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                    Show me more
                  </button>
                </div>
              )}

              {/* ── 3-tier fallbacks ── */}
              {showFallbacks && (
                <div className="fallback-congrats">
                  <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                    <path d="M10 2l1.8 5.4H18l-5 3.6 1.9 5.8L10 13.3l-4.9 3.5 1.9-5.8-5-3.6h6.2L10 2Z"
                      stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"/>
                  </svg>
                  <div>
                    <strong>
                      {enoughForShowMore
                        ? "Want to explore further?"
                        : "Congrats — you’ve watched most titles matching your filters!"}
                    </strong>
                    <span> Here are a few more suggestions with slightly relaxed options.</span>
                  </div>
                </div>
              )}

              {showFallbacks && fbPlatforms.length > 0 && (
                <>
                  <p className="fallback-label" style={{ marginTop: "0.6rem" }}>
                    <strong>Open to other platforms?</strong> — same genre &amp; language, broader platform selection:
                  </p>
                  <RecPosterGrid recs={fbPlatforms} {...sharedGridProps} />
                </>
              )}

              {showFallbacks && fbGenres.length > 0 && (
                <>
                  <p className="fallback-label" style={{ marginTop: "0.6rem" }}>
                    <strong>Open to other genres?</strong> — same platform &amp; language, different genres:
                  </p>
                  <RecPosterGrid recs={fbGenres} {...sharedGridProps} />
                </>
              )}

              {showFallbacks && fbLanguages.length > 0 && (
                <>
                  <p className="fallback-label" style={{ marginTop: "0.6rem" }}>
                    <strong>Open to other languages?</strong> — same platform &amp; genre, exploring English / Hindi / Telugu / Tamil:
                  </p>
                  <RecPosterGrid recs={fbLanguages} {...sharedGridProps} />
                </>
              )}
            </>
          )}
        </>
      )}

      {toasts.length > 0 && (
        <div className="toast-container">
          {toasts.map((t) => (
            <div key={t.id} className={`toast${t.exiting ? " toast-exit" : ""}`}>
              <span className="toast-icon"><CheckIcon /></span>
              <span>{t.message}</span>
            </div>
          ))}
        </div>
      )}
    </AppShell>
  );
}
