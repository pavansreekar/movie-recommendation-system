"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiRequest } from "../lib/api";
import { useSessionGuard } from "../components/useSessionGuard";

export default function HomePage() {
  const router = useRouter();
  const session = useSessionGuard();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ username: "", password: "" });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (!session.loading && session.authenticated) {
      router.replace("/dashboard");
    }
  }, [session, router]);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const payload = await apiRequest(path, {
        method: "POST",
        body: JSON.stringify(form),
      });
      if (mode === "login") {
        router.replace("/dashboard");
      } else {
        setMessage(payload.message);
        setMode("login");
      }
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="auth-fullscreen">
      {/* Animated ambient background */}
      <div className="auth-bg-blob auth-bg-blob-1" />
      <div className="auth-bg-blob auth-bg-blob-2" />
      <div className="auth-bg-blob auth-bg-blob-3" />
      <div className="auth-grid-texture" />

      <div className={`auth-center-card premium-card${mounted ? " auth-card-visible" : ""}`}>
        {/* Top gradient line */}
        <div className="auth-card-topline" />

        {/* Brand */}
        <div className="auth-brand-header">
          <div className="sidebar-brand-mark">N</div>
          <span className="auth-brand-name">NextPick</span>
        </div>

        {/* Headline */}
        <div className="auth-headline">
          <span className="eyebrow">
            {mode === "login" ? "Welcome back" : "Get started"}
          </span>
          <h1 className="auth-title">
            {mode === "login" ? "Sign in" : "Create account"}
          </h1>
          <p className="auth-subtitle">
            {mode === "login"
              ? "Your next great watch is waiting."
              : "Discover what to watch, personalized for you."}
          </p>
        </div>

        {/* Tab switcher */}
        <div className="auth-tab-row">
          <button
            className={`auth-tab${mode === "login" ? " auth-tab-active" : ""}`}
            onClick={() => { setMode("login"); setError(""); setMessage(""); }}
          >
            Login
          </button>
          <button
            className={`auth-tab${mode === "register" ? " auth-tab-active" : ""}`}
            onClick={() => { setMode("register"); setError(""); setMessage(""); }}
          >
            Create account
          </button>
        </div>

        {/* Form */}
        <form className="auth-form" onSubmit={handleSubmit}>
          <label className="auth-field-label">
            <span>Username</span>
            <div className="auth-input-wrap">
              <svg className="auth-input-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
              </svg>
              <input
                className="auth-input"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                placeholder="Enter your username"
                required
                minLength={3}
                autoComplete="username"
              />
            </div>
          </label>

          <label className="auth-field-label">
            <span>Password</span>
            <div className="auth-input-wrap">
              <svg className="auth-input-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
                <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
              <input
                className="auth-input"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder="Enter your password"
                required
                minLength={6}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>
          </label>

          <button className="auth-submit-btn" type="submit">
            <span>{mode === "login" ? "Sign in" : "Create account"}</span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14M12 5l7 7-7 7"/>
            </svg>
          </button>
        </form>

        {message && <div className="alert alert-success" style={{ marginTop: "1rem" }}>{message}</div>}
        {error && <div className="alert alert-warning" style={{ marginTop: "1rem" }}>{error}</div>}

        {/* Divider + features */}
        <div className="auth-features">
          {[
            { icon: "✦", text: "Weighted recommendations from your watch history" },
            { icon: "✦", text: "Live TMDB data — genres, cast, providers" },
            { icon: "✦", text: "Filter by mood, platform, language and genre" },
          ].map((f) => (
            <div key={f.text} className="auth-feature-item">
              <span className="auth-feature-dot">{f.icon}</span>
              <span className="auth-feature-text">{f.text}</span>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
