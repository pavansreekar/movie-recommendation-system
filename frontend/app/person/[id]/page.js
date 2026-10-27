"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import AppShell from "../../../components/AppShell";
import { apiRequest } from "../../../lib/api";
import { useSessionGuard } from "../../../components/useSessionGuard";
import { PageLoader } from "../../../components/Loading";

function FilmIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="20" height="20" rx="3" stroke="currentColor" strokeWidth="1.8" />
      <path d="M7 2v20M17 2v20M2 12h20M2 7h5M2 17h5M17 7h5M17 17h5"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
function TvIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="2" y="7" width="20" height="13" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8 7L12 3l4 4M8 20h8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M19 12H5M5 12l7-7M5 12l7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function PersonPage({ params }) {
  const { id } = use(params);
  const router = useRouter();
  const session = useSessionGuard();
  const user = session.user;
  const ready = !session.loading && session.authenticated;
  const [person, setPerson] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [bioExpanded, setBioExpanded] = useState(false);
  const [filter, setFilter] = useState("all"); // all | movie | tv

  useEffect(() => {
    if (!ready) return;
    setLoading(true);
    setError(null);
    setPerson(null);
    apiRequest(`/api/person/${id}`)
      .then(data => {
        if (data?.ok) setPerson(data.person);
        else setError("Person not found.");
      })
      .catch(err => setError(err?.message || "Failed to load person."))
      .finally(() => setLoading(false));
  }, [id, ready]);

  if (session.loading) {
    return <PageLoader messages={["Loading profile", "Gathering their films and shows"]} />;
  }
  if (!session.authenticated) return null;

  const filmography = person?.filmography || [];
  const filtered = filter === "all" ? filmography
    : filmography.filter(f => f.content_type === filter);

  let age = null;
  if (person?.birthday) {
    const born = new Date(person.birthday);
    const end = person.deathday ? new Date(person.deathday) : new Date();
    if (!Number.isNaN(born.getTime()) && !Number.isNaN(end.getTime())) {
      age = end.getFullYear() - born.getFullYear();
      const beforeBirthday =
        end.getMonth() < born.getMonth() ||
        (end.getMonth() === born.getMonth() && end.getDate() < born.getDate());
      if (beforeBirthday) age -= 1;
    }
  }

  const BIO_LIMIT = 300;
  const shortBio = person?.biography?.length > BIO_LIMIT
    ? person.biography.slice(0, BIO_LIMIT) + "…"
    : person?.biography;

  return (
    <AppShell user={user}>
      <div className="person-page">
        {loading && (
          <PageLoader compact messages={["Loading profile", "Gathering their films and shows"]} />
        )}

        {error && (
          <div className="person-error">
            <p>{error}</p>
            <button className="person-back-btn" onClick={() => router.back()}>
              <BackIcon /> Go back
            </button>
          </div>
        )}

        {person && !loading && (
          <>
            {/* Hero */}
            <div className="person-hero">
              <div className="person-hero-inner">
                <div className="person-profile-wrap">
                  {person.profile_url
                    ? <img src={person.profile_url} alt={person.name} className="person-profile-img" />
                    : <div className="person-profile-fallback">{person.name?.[0]}</div>
                  }
                </div>

                <div className="person-hero-info">
                  <button className="person-back-btn" onClick={() => router.back()}>
                    <BackIcon /> Back
                  </button>
                  <h1 className="person-name">{person.name}</h1>
                  <p className="person-dept">{person.known_for_department}</p>

                  <div className="person-meta-row">
                    {person.birthday && (
                      <span className="person-meta-chip">
                        🎂 {person.birthday}{person.deathday ? ` – ${person.deathday}` : ""}
                      </span>
                    )}
                    {age !== null && (
                      <span className="person-meta-chip">
                        {person.deathday ? `Died aged ${age}` : `${age} years old`}
                      </span>
                    )}
                    {person.place_of_birth && (
                      <span className="person-meta-chip">📍 {person.place_of_birth}</span>
                    )}
                  </div>

                  {person.biography && (
                    <div className="person-bio">
                      <p className="person-bio-text">
                        {bioExpanded ? person.biography : shortBio}
                      </p>
                      {person.biography.length > BIO_LIMIT && (
                        <button
                          className="person-bio-toggle"
                          onClick={() => setBioExpanded(v => !v)}
                        >
                          {bioExpanded ? "Show less" : "Read more"}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Filmography */}
            <div className="person-filmography">
              <div className="person-section-header">
                <h2 className="person-section-title">Filmography</h2>
                <div className="person-filter-tabs">
                  {["all", "movie", "tv"].map(f => (
                    <button
                      key={f}
                      className={`person-filter-tab${filter === f ? " active" : ""}`}
                      onClick={() => setFilter(f)}
                      type="button"
                    >
                      {f === "all" ? "All" : f === "movie" ? "Movies" : "TV Shows"}
                    </button>
                  ))}
                </div>
              </div>

              {filtered.length === 0 && (
                <p className="person-empty">No titles found.</p>
              )}

              <div className="person-grid">
                {filtered.map((item, i) => (
                  <Link
                    key={i}
                    href={`/title/${item.content_type}/${item.tmdb_id}`}
                    className="person-card"
                  >
                    {item.poster_url
                      ? <img src={item.poster_url} alt={item.title} className="person-card-poster" />
                      : <div className="person-card-poster person-card-poster-fallback">
                          {item.content_type === "tv" ? <TvIcon /> : <FilmIcon />}
                        </div>
                    }
                    <div className="person-card-body">
                      <span className="person-card-title">{item.title}</span>
                      <div className="person-card-meta">
                        {item.year && <span>{item.year}</span>}
                        {item.rating > 0 && <span>★ {item.rating.toFixed(1)}</span>}
                        <span className="person-card-type">{item.content_type === "tv" ? "TV" : "Film"}</span>
                      </div>
                      {item.character && (
                        <span className="person-card-role">{item.character}</span>
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
