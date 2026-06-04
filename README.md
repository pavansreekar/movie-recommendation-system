# NextPick 🎬

A personalised movie & TV recommendation app built with Flask + Next.js, powered by the [TMDB API](https://developer.themoviedb.org/) and an optional Claude AI chat assistant.

> **Live demo** · *replace with your URL after deploying*

---

## Features

### Recommendations
- **4-step wizard** — filter by format (Movie / TV Show), genre, platform, and language with multi-select
- **Smart scoring** — ranks titles using genre affinity, language match, watch-history patterns, and TMDB quality scores
- **"Show me more"** — when you have 16+ results, a button adds 8 extra titles and reveals three exploration sections:
  - *Open to other platforms?* — same genre & language, any platform
  - *Open to other genres?* — same platform & language, different genres
  - *Open to other languages?* — same platform & genre, exploring English / Hindi / Telugu / Tamil
- **Auto-fallbacks** — when results are scarce the same three sections appear automatically
- **Watch & replace** — marking a title as watched removes it and promotes the next one from a reserve pool

### Discovery
- **Today's Pick** — daily hero spotlight drawn from high-rated Telugu, Hindi, Tamil, and English titles
- **Trending** — real-time top searches from TMDB
- **Latest OTT Releases** — recent arrivals across Netflix, Prime, Hotstar, and more

### Title pages
- Full cast, genres, platform availability, TMDB rating
- Personal rating (1–10) and watched / watchlist toggles
- **Similar titles** — 3-tier discovery:
  1. Same language + same genres (e.g. Telugu Action/Thriller)
  2. Same genres, any language
  3. TMDB similar/recommendations filtered by genre overlap

### Chat assistant *(requires `ANTHROPIC_API_KEY`)*
- Natural-language queries: *"A Telugu thriller on Netflix"*, *"Movies like Interstellar"*
- Understands regional film industry names (Tollywood, Bollywood, Kollywood, K-drama …)
- Claude Haiku powers intent detection; falls back gracefully when key is absent

### Account
- Register / login with password hashing
- Watch history, ratings, and watchlist all persisted across sessions

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · Flask 3 |
| Frontend | Next.js 15 (App Router) · Framer Motion |
| Database | SQLite (auto-created, zero config) |
| Movie data | TMDB v3 REST API |
| AI chat | Anthropic Claude Haiku (optional) |
| Production server | Gunicorn |

---

## Project Structure

```
movie_bot/
├── web_app.py              # Flask entry point & all API routes
├── src/
│   ├── tmdb_client.py      # TMDB API client — LRU cache, retry, rate-limit handling
│   ├── recommender.py      # Scoring & ranking logic
│   ├── db.py               # SQLite wrapper — history, watchlist, ratings, chat
│   └── auth.py             # Password hashing (stdlib hashlib)
├── frontend/
│   ├── app/
│   │   ├── dashboard/      # Home — Today's Pick, Trending, OTT releases
│   │   ├── recommend/      # Wizard + recommendations + "Show me more"
│   │   ├── title/          # Title detail + similar titles
│   │   ├── search/         # TMDB search
│   │   ├── history/        # Watched history
│   │   └── watchlist/      # Saved watchlist
│   ├── components/
│   │   ├── AppShell.js     # Navigation shell
│   │   ├── ChatBot.js      # Floating AI chat widget
│   │   └── useSessionGuard.js
│   └── lib/
│       ├── api.js           # apiRequest() with timeout & unified errors
│       └── watchHistorySync.js
├── dev_run.py               # Starts both servers concurrently for local dev
├── Procfile                 # Gunicorn start command (Render / Heroku)
├── render.yaml              # Render Blueprint for one-click backend deploy
├── .env.example             # Environment variable template
├── requirements.txt
└── data/                   # SQLite DB — auto-created, git-ignored
```

---

## Local Development

### Prerequisites

| Tool | Minimum version |
|---|---|
| Python | 3.11 |
| Node.js | 18 |
| TMDB API key | free at [developer.themoviedb.org](https://developer.themoviedb.org/docs/getting-started) |

### 1. Clone & install

```bash
git clone https://github.com/pavansreekar/movie-recommendation-system.git
cd movie-recommendation-system

# Python deps
pip install -r requirements.txt

# Node deps
cd frontend && npm install && cd ..
```

### 2. Configure environment

```bash
cp .env.example .env
# open .env and fill in TMDB_API_KEY (and optionally ANTHROPIC_API_KEY)
```

Or export directly:

```bash
export TMDB_API_KEY="your_tmdb_v3_key"
export ANTHROPIC_API_KEY="your_anthropic_key"   # optional — enables chat
export FLASK_SECRET_KEY="any-random-string"
```

### 3. Start both servers

```bash
python dev_run.py
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:5001 |

To run servers individually:

```bash
python web_app.py              # backend only
cd frontend && npm run dev     # frontend only
```

---

## Deployment

The recommended setup is **Render** (Flask backend) + **Vercel** (Next.js frontend). Both have free tiers.

### Step 1 — Deploy the Flask backend to Render

**Option A — Blueprint (one click)**

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → **New → Blueprint** → connect your repo.  
   Render picks up `render.yaml` automatically and creates the `nextpick-api` service.
3. In the service's **Environment** tab add:

   | Variable | Value |
   |---|---|
   | `TMDB_API_KEY` | your TMDB v3 key |
   | `ANTHROPIC_API_KEY` | your Anthropic key *(optional)* |
   | `FRONTEND_ORIGINS` | leave blank for now — add your Vercel URL after step 2 |

4. **Deploy**. Copy the service URL, e.g. `https://nextpick-api.onrender.com`.

**Option B — Manual**

New → **Web Service** → connect repo → Runtime: **Python 3** →  
Build: `pip install -r requirements.txt` →  
Start: `gunicorn web_app:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120`

---

### Step 2 — Deploy the Next.js frontend to Vercel

1. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your GitHub repo.
2. Set **Root Directory** to `frontend`.
3. Add this environment variable:

   | Variable | Value |
   |---|---|
   | `BACKEND_ORIGIN` | your Render URL, e.g. `https://nextpick-api.onrender.com` |

4. **Deploy**. Copy the Vercel URL, e.g. `https://nextpick.vercel.app`.

---

### Step 3 — Wire CORS back to Render

In the Render dashboard → service → **Environment**, set:

```
FRONTEND_ORIGINS = https://nextpick.vercel.app
```

Trigger a redeploy. Your app is live. ✅

---

### SQLite in production

SQLite is fine for personal or low-traffic use. The database lives at `data/app.db` and is created automatically.

On Render's free tier the filesystem is **ephemeral** (resets on each deploy). Options:

- **Render Disk** (paid) — add a persistent disk, mount at `/data`, set env var `DB_PATH=/data/app.db` in `web_app.py`.
- **PostgreSQL** — swap `src/db.py` to use `psycopg2`; the schema is simple enough to port quickly.
- **Accept resets** — for a personal demo this is often fine; just re-register after each deploy.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TMDB_API_KEY` | *(required)* | TMDB v3 API key |
| `ANTHROPIC_API_KEY` | *(optional)* | Enables Claude chat assistant |
| `FLASK_SECRET_KEY` | `dev-secret-change-me` | Flask session signing — **change in production** |
| `BACKEND_ORIGIN` | `http://localhost:5001` | Backend URL used by Next.js rewrites |
| `FRONTEND_ORIGINS` | `http://localhost:3000,...` | Comma-separated CORS allowed origins |
| `TMDB_TIMEOUT_SECONDS` | `5` | Per-request TMDB timeout |
| `TMDB_MAX_RETRIES` | `2` | TMDB retry count |

Generate a strong secret key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## How Recommendations Work

### Request flow

```
Browser → Next.js (/api/…) → [next.config.mjs rewrite] → Flask (port 5001)
```

All `/api/*` paths are transparently proxied to Flask via Next.js rewrites. Cookies pass through because `fetch` always sets `credentials: "include"`.

### Scoring weights (per title)

| Signal | Weight |
|---|---|
| Genre match (per matched genre) | +4.0 |
| Platform match | +3.0 |
| Language match | +2.5 |
| Watch-history genre affinity (capped at 5 prior watches × 0.8) | 0 – 4.0 |
| TMDB quality prior (`rating × 0.15`) | variable |

### Fallback tiers

Triggered automatically when main results < 16, **or on-demand** via the *Show me more* button:

1. **Other platforms** — same genre & language, any platform
2. **Other genres** — same platform & language, any genre
3. **Other languages** — same platform & genre; explores English / Hindi / Telugu / Tamil

Each tier excludes already-watched titles and titles from prior tiers. Marking a title as watched auto-promotes the next one from a hidden reserve pool.

### Similar titles (title detail page)

| Tier | Source | Filter |
|---|---|---|
| 1 | TMDB Discover | Same language + same genres, sorted by rating |
| 2 | TMDB Discover | Same genres, any language |
| 3 | TMDB Similar / Recommendations | Genre overlap only (unrelated results dropped) |

### Chat assistant intent flow

```
Message received
  → fast greeting check        (no API calls — DB only)
  → TMDB metadata fetch        (cached in Flask session)
  → keyword extraction         (regex, no API)
  → Claude Haiku agent         (intent / filters / reference title)
  → TMDB discover or similar   (filtered catalog)
  → MovieRecommender           (scored & ranked)
  → JSON response
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/register` | Create account |
| `POST` | `/api/login` | Login |
| `GET` | `/api/session` | Auth state |
| `GET` | `/api/dashboard` | Home data (trending, OTT, today's pick) |
| `POST` | `/api/dashboard/recommend` | Filtered recommendations |
| `GET` | `/api/search?q=` | TMDB search |
| `GET` | `/api/title/<type>/<id>` | Title detail |
| `GET` | `/api/title/<type>/<id>/similar` | Similar titles |
| `POST` | `/api/title/<type>/<id>/watch` | Toggle watched |
| `POST` | `/api/title/<type>/<id>/watchlist` | Toggle watchlist |
| `POST` | `/api/title/<type>/<id>/rate` | Set rating (1–10) |
| `POST` | `/api/chat` | Chat message → AI reply + recs |
| `GET` | `/api/chat/history` | Conversation history |
| `POST` | `/api/chat/clear` | Clear conversation |

---

## Contributing

Pull requests are welcome. Open an issue first for larger changes.

```bash
# Backend — auto-reload on file changes
FLASK_DEBUG=1 python web_app.py

# Frontend — Next.js hot reload
cd frontend && npm run dev
```

Delete `data/app.db` at any time to reset all user data.

---

## License

MIT
