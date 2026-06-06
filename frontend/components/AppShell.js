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
function SearchGrid({ results, query, onSelect, onClose }) {
  if (!results.length) {
    return (
      <div className="sg-overlay">
        <div className="sg-empty">
          <p className="sg-empty-text">No results for <strong>"{query}"</strong></p>
          <p className="sg-empty-sub">Try a different title, person, or genre.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="sg-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sg-grid">
        {results.map((item) => (
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

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (!q) { setResults([]); return; }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await apiRequest(`/api/search/live?query=${encodeURIComponent(q)}`);
        setResults(data.results || []);
      } catch { setResults([]); }
      finally { setLoading(false); }
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

/* ── AppShell ─────────────────────────────────────────────────────────────── */
export default function AppShell({ user, children }) {
  const pathname = usePathname();
  const router   = useRouter();

  const [searchQuery,   setSearchQuery]   = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchActive,  setSearchActive]  = useState(false);
  const debounceRef = useRef(null);
  const inputRef    = useRef(null);

  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);
  const [theme, setTheme] = useState("dark");

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
    if (!q) { setSearchResults([]); setSearchActive(false); return; }
    setSearchActive(true);
    setSearchLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await apiRequest(`/api/search/live?query=${encodeURIComponent(q)}`);
        setSearchResults(data.results || []);
      } catch { setSearchResults([]); }
      finally { setSearchLoading(false); }
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

  useEffect(() => {
    const fn = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setDropdownOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, []);

  function clearSearch() { setSearchQuery(""); setSearchResults([]); setSearchActive(false); }
  function handleSelect(item) { clearSearch(); router.push(`/title/${item.content_type}/${item.tmdb_id}`); }
  async function handleLogout() {
    await apiRequest("/api/auth/logout", { method: "POST" });
    router.replace("/");
  }

  const isLight = theme === "light";

  return (
    <div className="app-shell">
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
            {/* Desktop search bar */}
            <div className={`nav-search-bar${searchActive ? " nav-search-active" : ""}`}>
              <span className="nav-search-icon"><SearchIcon /></span>
              <input
                ref={inputRef}
                type="text"
                placeholder="Titles, people, genres…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label="Search"
                autoComplete="off"
              />
              {searchActive && (
                <button className="nav-search-clear" onClick={clearSearch} aria-label="Clear" type="button">
                  <CloseSearchIcon />
                </button>
              )}
              {searchLoading && <span className="nav-search-spinner" />}
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
          query={searchQuery.trim()}
          onSelect={handleSelect}
          onClose={clearSearch}
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
