# NextPick

A personalised movie and TV recommendation app built with a Flask backend and a Next.js frontend, powered by the TMDB API.

## Features

- **4-step recommendation wizard** — format (Movie / TV Show), genres (multi-select), platforms (multi-select), languages (multi-select)
- **Smart 3-tier fallbacks** — when your history exhausts filtered picks, the app surfaces titles across other platforms, other genres, or other languages
- **Today's Pick** — a daily hero spotlight seeded by date, drawn from high-quality English, Hindi, Telugu, and Tamil titles
- **Watchlist & watch history** — save titles; history drives the recommendation scorer
- **Global search** — look up any title on TMDB and add it to your history or watchlist

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12 + Flask |
| Frontend | Next.js 15 (App Router) |
| Database | SQLite |
| Movie data | TMDB v3 API |

---

## Project Structure

```
movie_bot/
├── web_app.py            # Flask entry point & all API routes
├── src/
│   ├── tmdb_client.py    # TMDB API client with in-memory cache
│   ├── recommender.py    # Scoring & ranking logic
│   ├── db.py             # SQLite wrapper (history, watchlist, ratings)
│   └── auth.py           # Password hashing
├── frontend/
│   ├── app/
│   │   ├── dashboard/    # Home page — Today's Pick, Trending, OTT releases
│   │   ├── recommend/    # 4-step wizard + recommendations
│   │   ├── search/       # Title search
│   │   ├── history/      # Watched history
│   │   └── watchlist/    # Saved watchlist
│   ├── components/       # AppShell, useSessionGuard
│   └── lib/              # apiRequest, watchHistorySync
├── dev_run.py            # Starts both servers concurrently (development)
├── requirements.txt
└── data/                 # SQLite DB — auto-created, git-ignored
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- A free [TMDB API key](https://developer.themoviedb.org/docs/getting-started)

### 1. Install dependencies

```bash
# Python
pip install -r requirements.txt

# Node
cd frontend && npm install && cd ..
```

### 2. Set environment variables

```bash
export TMDB_API_KEY="your_tmdb_v3_key"
export FLASK_SECRET_KEY="change-me-in-production"
```

### 3. Run in development

```bash
python dev_run.py
```

- Frontend → http://localhost:3000  
- Backend API → http://localhost:5001

To run servers individually:

```bash
python web_app.py          # backend only
cd frontend && npm run dev # frontend only
```

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TMDB_API_KEY` | *(required)* | TMDB v3 API key |
| `FLASK_SECRET_KEY` | `dev-secret-change-me` | Flask session signing key |
| `BACKEND_ORIGIN` | `http://localhost:5001` | Backend URL used by Next.js rewrites |
| `FRONTEND_ORIGINS` | `http://localhost:3000,...` | Comma-separated allowed CORS origins |
| `TMDB_TIMEOUT_SECONDS` | `5` | Per-request TMDB timeout |
| `TMDB_MAX_RETRIES` | `2` | TMDB retry count |

---

## How Recommendations Work

### Scoring (per title)

| Signal | Points |
|---|---|
| Genre match (per matched genre) | +4.0 |
| Language match | +2.5 |
| Watch-history genre affinity | up to +4.0 |
| TMDB quality prior (rating × 0.15) | variable |

### Fallback tiers (shown when main results < 16)

1. **Other platforms** — same genre & language, any platform  
2. **Other genres** — same platform & language, any genre  
3. **Other languages** — same platform & genre, explores English / Hindi / Telugu / Tamil

Every fallback excludes already-watched titles and titles shown in previous tiers. Each section shows 8 titles with a reserve pool that auto-promotes when items are marked as watched.

---

## Deployment

- Set `FLASK_SECRET_KEY` to a strong random string.
- `data/app.db` is auto-created and git-ignored — safe to delete to reset all user data.
- For production: run Flask behind Gunicorn, build Next.js with `npm run build && npm start`.
