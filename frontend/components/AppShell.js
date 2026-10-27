"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { apiRequest } from "../lib/api";
import ChatBot from "./ChatBot";

const navItems = [
  { href: "/dashboard",  label: "Home",      icon: HomeIcon },
  { href: "/recommend",  label: "Recommend",  icon: StarIcon },
  { href: "/watchlist",  label: "Watchlist",  icon: BookmarkIcon },
  { href: "/history",    label: "History",    icon: ClockIcon },
];

/* ── Icons ────────────────────────────────────────────────────────────────── */
function SearchIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="M16.5 16.5l4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
function SearchIconLg() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
      <path d="M16.5 16.5l4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
function CloseSearchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}
function ChevronDown() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 8" fill="none" aria-hidden="true">
      <path d="M1 1.5l5 5 5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function SunIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="5" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
function MoonIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function GlobeIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
function HomeIcon({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V9.5Z"
        stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M9 21V12h6v9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function StarIcon({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6L12 2Z"
        stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
function BookmarkIcon({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5 3h14a1 1 0 0 1 1 1v17l-8-4.7L4 21V4a1 1 0 0 1 1-1Z"
        stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}
function ClockIcon({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 7v5.2l3 2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function getInitials(username) {
  if (!username) return "U";
  return username.slice(0, 1).toUpperCase();
}

/* ── Desktop search grid overlay ─────────────────────────────────────────── */
const SEARCH_TABS = ["All", "People", "Movies", "TV Shows"];

function SearchGrid({ results, people, query, loading, onSelect, onClose, router }) {
  const [tab, setTab] = useState("All");

  const movies = results.filter(r => r.content_type === "movie");
  const tvShows = results.filter(r => r.content_type === "tv");

  const tabItems = tab === "All" ? results
    : tab === "People" ? []
    : tab === "Movies" ? movies
    : tvShows;

  const tabPeople = tab === "All" ? (people || []).slice(0, 3) : tab === "People" ? (people || []) : [];

  const isEmpty = tabItems.length === 0 && tabPeople.length === 0;

  return (
    <div className="sg-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sg-panel">
        {/* Filter tabs */}
        <div className="sg-tabs">
          {SEARCH_TABS.map(t => (
            <button
              key={t}
              className={`sg-tab${tab === t ? " sg-tab-active" : ""}`}
              onClick={() => setTab(t)}
              type="button"
            >
              {t}
              {t === "People" && people?.length > 0 && <span className="sg-tab-count">{people.length}</span>}
              {t === "Movies" && <span className="sg-tab-count">{movies.length}</span>}
              {t === "TV Shows" && <span className="sg-tab-count">{tvShows.length}</span>}
            </button>
          ))}
        </div>

        {isEmpty && loading && (
          <div className="sg-empty" aria-live="polite">
            <span className="nav-search-spinner sg-empty-spinner" aria-hidden="true" />
            <p className="sg-empty-sub">Searching for <strong>"{query}"</strong>…</p>
          </div>
        )}

        {isEmpty && !loading && (
          <div className="sg-empty">
            <p className="sg-empty-text">No results for <strong>"{query}"</strong></p>
            <p className="sg-empty-sub">Try a different title, person, or genre.</p>
          </div>
        )}

        {/* People row */}
        {tabPeople.length > 0 && (
          <div className="sg-people-row">
            <span className="sg-section-label">People</span>
            <div className="sg-people-list">
              {tabPeople.map(p => (
                <button
                  key={p.id}
                  className="sg-person-card"
                  type="button"
                  onClick={() => { onClose(); router.push(`/person/${p.id}`); }}
                >
                  {p.profile_url
                    ? <img src={p.profile_url} alt={p.name} className="sg-person-avatar" />
                    : <div className="sg-person-avatar sg-person-avatar-fallback">{p.name?.[0]}</div>
                  }
                  <div className="sg-person-info">
                    <span className="sg-person-name">{p.name}</span>
                    <span className="sg-person-dept">{p.known_for_department}</span>
                    {p.known_for?.length > 0 && (
                      <span className="sg-person-known">{p.known_for.slice(0, 2).join(", ")}</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Title grid */}
        {tabItems.length > 0 && (
          <>
            {tabPeople.length > 0 && <span className="sg-section-label sg-section-label-titles">Titles</span>}
            <div className="sg-grid">
              {tabItems.map(item => (
                <button
                  key={`${item.content_type}-${item.tmdb_id}`}
                  className="sg-card"
                  onClick={() => onSelect(item)}
                  type="button"
                >
                  {item.poster_url
                    ? <img src={item.poster_url} alt={item.title} className="sg-poster" />
                    : <div className="sg-poster sg-poster-fallback"><span>{item.title?.[0]}</span></div>
                  }
                  <div className="sg-card-overlay">
                    <span className="sg-card-title">{item.title}</span>
                    <span className="sg-card-meta">
                      {item.content_label}{item.year ? ` · ${item.year}` : ""}
                      {item.rating ? ` · ★ ${item.rating.toFixed(1)}` : ""}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ── Mobile search overlay ────────────────────────────────────────────────── */
function MobileSearch({ onClose, onSelect }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  const debounceRef = useRef(null);
  const requestRef = useRef(0);

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    const request = ++requestRef.current;   // a reply to an older query is ignored
    if (!q) { setResults([]); setLoading(false); return; }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await apiRequest(`/api/search/live?query=${encodeURIComponent(q)}`);
        if (request === requestRef.current) setResults(data.results || []);
      } catch { if (request === requestRef.current) setResults([]); }
      finally { if (request === requestRef.current) setLoading(false); }
    }, 220);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  useEffect(() => {
    const fn = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", fn);
    return () => document.removeEventListener("keydown", fn);
  }, [onClose]);

  return (
    <div className="mobile-search-overlay">
      <div className="mobile-search-bar-row">
        <div className="mobile-search-input-wrap">
          <span className="nav-search-icon"><SearchIcon /></span>
          <input
            ref={inputRef}
            type="text"
            className="mobile-search-input"
            placeholder="Titles, people, genres…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoComplete="off"
          />
          {loading && <span className="nav-search-spinner" />}
          {query && !loading && (
            <button className="nav-search-clear" onClick={() => setQuery("")} type="button" aria-label="Clear">
              <CloseSearchIcon />
            </button>
          )}
        </div>
        <button className="mobile-search-cancel" onClick={onClose} type="button">Cancel</button>
      </div>

      {query.trim() && results.length === 0 && !loading && (
        <div className="mobile-search-empty">No results for <strong>"{query}"</strong></div>
      )}

      {results.length > 0 && (
        <div className="mobile-search-results">
          {results.map((item) => (
            <button
              key={`${item.content_type}-${item.tmdb_id}`}
              className="mobile-search-item"
              onClick={() => { onClose(); onSelect(item); }}
              type="button"
            >
              {item.poster_url
                ? <img src={item.poster_url} alt={item.title} className="mobile-search-poster" />
                : <div className="mobile-search-poster mobile-search-poster-fallback">{item.title?.[0]}</div>
              }
              <div className="mobile-search-info">
                <span className="mobile-search-title">{item.title}</span>
                <span className="mobile-search-meta">
                  {item.content_label}{item.year ? ` · ${item.year}` : ""}
                  {item.rating ? ` · ★ ${item.rating.toFixed(1)}` : ""}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Custom Cursor ────────────────────────────────────────────────────────── */
function CustomCursor() {
  const dotRef   = useRef(null);
  const ringRef  = useRef(null);
  const posRef   = useRef({ x: -100, y: -100 });
  const ringPos  = useRef({ x: -100, y: -100 });
  const rafRef   = useRef(null);

  useEffect(() => {
    const isTouchDevice = window.matchMedia("(hover: none)").matches;
    if (isTouchDevice) return;

    function onMove(e) {
      posRef.current = { x: e.clientX, y: e.clientY };
      if (dotRef.current) {
        dotRef.current.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
      }
    }

    function lerp(a, b, t) { return a + (b - a) * t; }
    function animate() {
      ringPos.current.x = lerp(ringPos.current.x, posRef.current.x, 0.12);
      ringPos.current.y = lerp(ringPos.current.y, posRef.current.y, 0.12);
      if (ringRef.current) {
        ringRef.current.style.transform = `translate(${ringPos.current.x}px, ${ringPos.current.y}px)`;
      }
      rafRef.current = requestAnimationFrame(animate);
    }

    function onEnterInteractive() { dotRef.current?.classList.add("cursor-hover"); ringRef.current?.classList.add("cursor-hover"); }
    function onLeaveInteractive() { dotRef.current?.classList.remove("cursor-hover"); ringRef.current?.classList.remove("cursor-hover"); }

    document.addEventListener("mousemove", onMove);
    rafRef.current = requestAnimationFrame(animate);

    const interactives = document.querySelectorAll("a, button, input, [role='button']");
    interactives.forEach(el => {
      el.addEventListener("mouseenter", onEnterInteractive);
      el.addEventListener("mouseleave", onLeaveInteractive);
    });

    return () => {
      document.removeEventListener("mousemove", onMove);
      cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return (
    <>
      <div ref={dotRef}  className="cursor-dot"  aria-hidden="true" />
      <div ref={ringRef} className="cursor-ring" aria-hidden="true" />
    </>
  );
}

/* ── AppShell ─────────────────────────────────────────────────────────────── */
function getPageTheme(pathname) {
  if (!pathname) return "dashboard";
  if (pathname.startsWith("/recommend")) return "recommend";
  if (pathname.startsWith("/watchlist"))  return "watchlist";
  if (pathname.startsWith("/history"))    return "history";
  if (pathname.startsWith("/title"))      return "title";
  return "dashboard";
}

export default function AppShell({ user, children }) {
  const pathname = usePathname();
  const router   = useRouter();

  const [searchQuery,    setSearchQuery]    = useState("");
  const [searchResults,  setSearchResults]  = useState([]);
  const [searchPeople,   setSearchPeople]   = useState([]);
  const [searchLoading,  setSearchLoading]  = useState(false);
  const [searchActive,   setSearchActive]   = useState(false);
  const [searchExpanded, setSearchExpanded] = useState(false);
  const debounceRef = useRef(null);
  const searchRequestRef = useRef(0);
  const inputRef    = useRef(null);
  const searchBarRef = useRef(null);

  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);
  const [theme, setTheme] = useState("dark");

  // Region: decides which streaming platforms and availability the user sees.
  const [regions, setRegions] = useState([]);
  const [region, setRegion] = useState(user?.region || "");
  const [regionSaving, setRegionSaving] = useState(false);

  useEffect(() => {
    let active = true;
    // Same request the session guard makes — served from the in-memory cache.
    apiRequest("/api/session")
      .then((data) => {
        if (!active) return;
        setRegions(data.regions || []);
        if (data.user?.region) setRegion(data.user.region);
      })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  async function handleRegionChange(event) {
    const next = event.target.value;
    if (!next || next === region || regionSaving) return;
    const previous = region;
    setRegion(next);
    setRegionSaving(true);
    try {
      await apiRequest("/api/settings/region", { method: "POST", body: JSON.stringify({ region: next }) });
      // Platforms, availability and recommendations all depend on the region.
      window.location.reload();
    } catch {
      setRegion(previous);
      setRegionSaving(false);
    }
  }

  useEffect(() => {
    const saved = (typeof window !== "undefined" && localStorage.getItem("np-theme")) || "dark";
    setTheme(saved);
    document.documentElement.setAttribute("data-theme", saved);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("np-theme", next);
    document.documentElement.setAttribute("data-theme", next);
  }

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = searchQuery.trim();
    const request = ++searchRequestRef.current;   // a reply to an older query is ignored
    if (!q) { setSearchResults([]); setSearchActive(false); setSearchLoading(false); return; }
    setSearchActive(true);
    setSearchLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await apiRequest(`/api/search/live?query=${encodeURIComponent(q)}`);
        if (request !== searchRequestRef.current) return;
        setSearchResults(data.results || []);
        setSearchPeople(data.people || []);
      } catch {
        if (request === searchRequestRef.current) { setSearchResults([]); setSearchPeople([]); }
      }
      finally { if (request === searchRequestRef.current) setSearchLoading(false); }
    }, 220);
    return () => clearTimeout(debounceRef.current);
  }, [searchQuery]);

  useEffect(() => {
    setSearchQuery(""); setSearchResults([]); setSearchActive(false);
    setMobileSearchOpen(false);
  }, [pathname]);

  useEffect(() => {
    const fn = (e) => { if (e.key === "Escape") clearSearch(); };
    document.addEventListener("keydown", fn);
    return () => document.removeEventListener("keydown", fn);
  }, []);

  // "/" focuses the search field, unless the user is already typing somewhere.
  useEffect(() => {
    const fn = (e) => {
      if (e.key !== "/" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (!inputRef.current || inputRef.current.offsetParent === null) return;
      e.preventDefault();
      inputRef.current.focus();
    };
    document.addEventListener("keydown", fn);
    return () => document.removeEventListener("keydown", fn);
  }, []);

  useEffect(() => {
    const fn = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setDropdownOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, []);

  // Collapse search bar when clicking outside it
  useEffect(() => {
    const fn = (e) => {
      if (searchBarRef.current && !searchBarRef.current.contains(e.target)) {
        if (!searchQuery) setSearchExpanded(false);
      }
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [searchQuery]);

  function clearSearch() { setSearchQuery(""); setSearchResults([]); setSearchPeople([]); setSearchActive(false); setSearchExpanded(false); }
  function handleSelect(item) { clearSearch(); router.push(`/title/${item.content_type}/${item.tmdb_id}`); }
  async function handleLogout() {
    await apiRequest("/api/auth/logout", { method: "POST" });
    router.replace("/");
  }

  const isLight = theme === "light";

  return (
    <div className="app-shell" data-page={getPageTheme(pathname)}>
      {/* ── Top nav ── */}
      <nav className="top-nav">
        <div className="top-nav-inner">
          <Link href="/dashboard" className="nav-brand" aria-label="NextPick home">
            <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true" style={{ flexShrink: 0 }}>
              <defs>
                <linearGradient id="np-logo-bg" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#C47820" />
                  <stop offset="100%" stopColor="#E8A23A" />
                </linearGradient>
              </defs>
              <rect width="32" height="32" rx="8" fill="url(#np-logo-bg)" />
              <g stroke="white" strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" fill="none">
                <line x1="8" y1="5.5" x2="8" y2="26.5" />
                <line x1="24" y1="5.5" x2="24" y2="26.5" />
                <line x1="24" y1="5.5" x2="8" y2="26.5" />
              </g>
            </svg>
            <span className="eyebrow">NextPick</span>
          </Link>

          {/* Desktop nav links */}
          <div className="top-nav-links">
            {navItems.map(({ href, label }) => (
              <Link key={href} href={href}
                className={`top-nav-link${pathname === href ? " active" : ""}`}>
                {label}
              </Link>
            ))}
          </div>

          <div className="nav-right">
            {/* Desktop collapsing search bar */}
            <div
              ref={searchBarRef}
              className={`nav-search-bar${searchExpanded ? " nav-search-expanded" : ""}${searchActive ? " nav-search-active" : ""}`}
              onMouseEnter={() => setSearchExpanded(true)}
              onClick={(e) => { if (e.target === e.currentTarget) inputRef.current?.focus(); }}
            >
              <button
                className="nav-search-icon-btn"
                type="button"
                aria-label="Search"
                onClick={() => { setSearchExpanded(true); setTimeout(() => inputRef.current?.focus(), 50); }}
              >
                <SearchIcon />
              </button>
              <input
                ref={inputRef}
                className="nav-search-input"
                type="text"
                placeholder="Titles, people, genres…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label="Search"
                autoComplete="off"
                spellCheck={false}
                onFocus={() => setSearchExpanded(true)}
              />
              <div className="nav-search-trailing">
                {searchLoading && <span className="nav-search-spinner" aria-hidden="true" />}
                {searchActive && (
                  <button className="nav-search-clear" onClick={clearSearch} aria-label="Clear" type="button">
                    <CloseSearchIcon />
                  </button>
                )}
                {!searchQuery && <kbd className="nav-search-kbd" aria-hidden="true">/</kbd>}
              </div>
            </div>

            {/* Mobile search trigger — only visible on mobile via CSS */}
            <button
              className="mobile-search-trigger"
              onClick={() => setMobileSearchOpen(true)}
              aria-label="Search"
              type="button"
            >
              <SearchIconLg />
            </button>

            {/* User dropdown */}
            <div className="nav-user" ref={dropdownRef}>
              <button
                className="nav-user-btn"
                onClick={() => setDropdownOpen((v) => !v)}
                aria-label="Account menu"
                aria-expanded={dropdownOpen}
              >
                <span className="user-avatar" aria-hidden="true">{getInitials(user?.username)}</span>
                <ChevronDown />
              </button>

              {dropdownOpen && (
                <div className="nav-dropdown" role="menu">
                  <div className="nav-dropdown-header">
                    <span className="nav-dropdown-username">{user?.username || "User"}</span>
                  </div>
                  <div className="nav-dropdown-divider" />
                  <div className="nav-dropdown-theme-row">
                    <span className="nav-dropdown-theme-label">
                      {isLight ? <SunIcon /> : <MoonIcon />}
                      {isLight ? "Light mode" : "Dark mode"}
                    </span>
                    <button
                      className={`theme-toggle-btn${isLight ? " light-active" : ""}`}
                      onClick={toggleTheme}
                      aria-label={isLight ? "Switch to dark mode" : "Switch to light mode"}
                    />
                  </div>
                  {regions.length > 0 && (
                    <div className="nav-dropdown-region-row">
                      <span className="nav-dropdown-theme-label">
                        <GlobeIcon />
                        Region
                      </span>
                      <select
                        className="nav-region-select"
                        value={region}
                        onChange={handleRegionChange}
                        disabled={regionSaving}
                        aria-label="Your region — sets which streaming platforms are shown"
                      >
                        {regions.map((r) => (
                          <option key={r.code} value={r.code}>{r.name}</option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div className="nav-dropdown-divider" />
                  <button className="nav-dropdown-item" onClick={handleLogout} role="menuitem">
                    Logout
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </nav>

      <main className="main-shell">{children}</main>

      {/* ── Mobile bottom navigation ── */}
      <nav className="bottom-nav" aria-label="Mobile navigation">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href}
            className={`bottom-nav-item${pathname === href ? " active" : ""}`}>
            <span className="bottom-nav-icon"><Icon size={22} /></span>
            <span className="bottom-nav-label">{label}</span>
          </Link>
        ))}
        <button
          className={`bottom-nav-item${mobileSearchOpen ? " active" : ""}`}
          onClick={() => setMobileSearchOpen(true)}
          type="button"
          aria-label="Search"
        >
          <span className="bottom-nav-icon"><SearchIconLg /></span>
          <span className="bottom-nav-label">Search</span>
        </button>
      </nav>

      {/* Desktop search overlay */}
      {searchActive && (
        <SearchGrid
          results={searchResults}
          people={searchPeople}
          query={searchQuery.trim()}
          loading={searchLoading}
          onSelect={handleSelect}
          onClose={clearSearch}
          router={router}
        />
      )}

      {/* Mobile search overlay */}
      {mobileSearchOpen && (
        <MobileSearch
          onClose={() => setMobileSearchOpen(false)}
          onSelect={handleSelect}
        />
      )}

      <ChatBot />
    </div>
  );
}
