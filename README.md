# NextPick 🎬

**Stop scrolling. Find your next movie or show.**

NextPick is a free movie and TV recommendation website. Tell it what you're in the mood for and it suggests titles based on your choices and what you've already watched, shows where each one is streaming in your country, and keeps your watchlist, history and ratings in one place. You can also just ask its chat assistant in plain words.

### [Try it live → yournextpick.vercel.app](https://yournextpick.vercel.app)

| Home | Recommendations |
|---|---|
| ![Home page with Today's Pick](docs/screenshots/home.jpg) | ![Recommendations filtered by format, genre, platform and language](docs/screenshots/recommendations.jpg) |

| Title page | Chat assistant |
|---|---|
| ![Title page with rating, trailer and where to stream](docs/screenshots/title.jpg) | ![Chat assistant answering a follow-up question](docs/screenshots/chat.jpg) |

---

## Features

### Recommendations
- **4-step wizard** — choose format, genre, platform and language. Pick several in each step, or skip any step to leave it open
- **Personalised ranking** — blends your choices, TMDB ratings and a taste profile built from your watch history
- **"Don't mind rewatching"** — also brings back favourites from your own history that match your choices
- **"Since you've watched…"** — picks seeded from your latest watch, sharing its genres or lead actors
- **"Show me more"** — adds up to 8 extra titles and opens three exploration sections:
  - *Open to other platforms?* — same genre and language, any platform
  - *Open to other genres?* — same platform and language, different genres
  - *Open to other languages?* — same platform and genre, exploring English, Hindi, Telugu and Tamil
- **Auto-fallbacks** — the same three sections appear by themselves when results are scarce
- **Watch & replace** — marking a title as watched removes it and promotes the next one from a reserve pool

### Discovery
- **Today's Pick** — one standout title each day, chosen from high-rated English, Hindi, Telugu and Tamil movies and shows
- **Top searches** — TMDB's daily trending list
- **Recent streaming arrivals** — the last 60 days on the main services in your region
- **Live search** — titles, actors and directors appear as you type. Press `/` on any page to jump straight in

### Title and people pages
- Overview, cast, genres, runtime, TMDB rating and the official age rating for your region
- Where to stream it in your region
- The official trailer plays right on the page
- Rate titles from 1 to 10, mark them as watched or save them to your watchlist
- **Similar titles** — 3-tier discovery:
  1. Same language and genres (e.g. Telugu Action/Thriller)
  2. Same genres, any language
  3. TMDB similar titles and recommendations, filtered by genre overlap
- **Actor and director pages** — a short biography and filmography, most popular first

### Chat assistant *(requires `GROQ_API_KEY`)*
- Natural-language queries: *"A Telugu thriller on Netflix"*, *"Movies like Interstellar but not animated"*, *"Hindi movies under 2 hours"*, *"Christopher Nolan films from the 2000s"*
- Franchises, characters and subjects: *"Batman movies"*, *"Star Wars films on JioHotstar"*, *"movies about time travel"*, *"zombie shows"*
- Follow-ups keep the context: *"on Prime instead"*, *"newer ones"*, *"tell me about the second one"*
- Answers questions about a title (director, runtime, where to watch in your region) from TMDB data
- Manages your library: *"add Kantara to my watchlist"*, *"I watched Dune Part Two"*
- Understands regional film industry names (Tollywood, Bollywood, Kollywood, K-drama …)
- Runs on Groq (`openai/gpt-oss-120b`, falling back to `openai/gpt-oss-20b`). Falls back to keyword matching when the key is absent or the model is unreachable (Groq refuses connections from many VPNs — see `TMDB_API_HOST` below for running without one)

### Your account
- **27 countries** — platforms, availability and age ratings follow the region you choose
- **Your library** — watch history, ratings and watchlist, filterable by type, genre and language
- **Light and dark themes** — your choice is remembered on each device
- **Secure sign-in** — passwords are hashed with scrypt, and you stay signed in for 30 days through an HTTP-only cookie
- **Built for slow moments** — branded loading screens and placeholders, automatic retries while the server wakes up, and a retry button if a section fails

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | JavaScript · React 19 · Next.js 15 (App Router) · Framer Motion · Tabler Icons · hand-written CSS |
| Backend | Python · Flask 3 · Gunicorn |
| Database | PostgreSQL on Neon (live site) · SQLite (local development) |
| Recommendations | Rule-based scoring + content-based similarity, in plain Python with no ML libraries |
| Movie data | TMDB v3 REST API |
| AI chat | Groq (`openai/gpt-oss-120b`, falling back to `openai/gpt-oss-20b`) — optional |
| Hosting | Vercel (frontend) · Render (backend) · Neon (database) |
| Uptime | cron-job.org keep-alive ping |

### How it fits together

```mermaid
flowchart LR
    B[Browser] --> V["Next.js frontend<br/>Vercel"]
    V -- "/api/*" --> F["Flask API<br/>Render"]
    F --> D[("PostgreSQL<br/>Neon")]
    F --> T["TMDB<br/>movie data"]
    F --> G["Groq<br/>AI chat"]
    C["cron-job.org<br/>ping every 5 min"] --> F
```

---

## Project Structure

```
movie-recommendation-system/
├── web_app.py              # Flask app: API routes, recommendation pipeline, chat agent
├── src/
│   ├── tmdb_client.py      # TMDB client: caching, retries, rate limits, host fallback
│   ├── recommender.py      # Hybrid scoring and ranking
│   ├── db.py               # PostgreSQL / SQLite layer: accounts, history, ratings, watchlist, chat
│   └── auth.py             # scrypt password hashing
├── frontend/
│   ├── app/
│   │   ├── page.js         # Sign in / create account
│   │   ├── dashboard/      # Home: Today's Pick, top searches, streaming arrivals
│   │   ├── recommend/      # Wizard, recommendations, "Show me more"
│   │   ├── title/          # Title page, trailer, similar titles
│   │   ├── person/         # Actor and director pages
│   │   ├── search/         # Full search results
│   │   ├── history/        # Watch history and ratings
│   │   ├── watchlist/      # Saved titles
│   │   └── api/            # Backup /api proxy (the rewrite in next.config.mjs answers first)
│   ├── components/
│   │   ├── AppShell.js     # Navigation, search, account menu
│   │   ├── ChatBot.js      # Floating chat assistant
│   │   ├── Loading.js      # Loading screens, placeholders, error states
│   │   └── useSessionGuard.js
│   ├── lib/
│   │   ├── api.js          # fetch wrapper: timeouts, retries, unified errors
│   │   └── watchHistorySync.js
│   └── next.config.mjs     # Forwards /api/* to Flask
├── docs/screenshots/       # Images used in this README
├── dev_run.py              # Starts both servers for local development
├── Procfile                # Gunicorn start command
├── render.yaml             # Render Blueprint for the backend
├── .env.example            # Environment variable template
├── requirements.txt
└── data/                   # Local SQLite database, created automatically, git-ignored
```

---

## Local Development

### Prerequisites

| Tool | Minimum version |
|---|---|
| Python | 3.11 |
| Node.js | 18.18 |
| TMDB API key | free at [developer.themoviedb.org](https://developer.themoviedb.org/docs/getting-started) |

No database setup is needed. Without `DATABASE_URL`, the app creates a SQLite database at `data/app.db` by itself.

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
# open .env and fill in TMDB_API_KEY (and optionally GROQ_API_KEY)
```

Or export directly:

```bash
export TMDB_API_KEY="your_tmdb_v3_key"
export GROQ_API_KEY="your_groq_key"             # optional — enables chat
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

The live site runs on **Vercel** (Next.js frontend), **Render** (Flask backend) and **Neon** (PostgreSQL), all on free plans.

### Step 1 — Create a free PostgreSQL database (Neon)

Render's free plan has an **ephemeral filesystem**, so anything saved on its disk is lost on every restart. User data lives in Neon instead, and the app refuses to start on Render without a `DATABASE_URL`.

1. Go to [neon.tech](https://neon.tech) and sign up for free.
2. Click **New Project** → give it a name (e.g. `nextpick`) → pick the region closest to your Render service → **Create Project**.
3. On the project dashboard, copy the **Connection string** (looks like `postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`).
4. Keep this string handy — you'll add it to Render in the next step.

---

### Step 2 — Deploy the Flask backend to Render

**Option A — Blueprint (one click)**

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → **New → Blueprint** → connect your repo.  
   Render picks up `render.yaml` automatically, creates the `nextpick-api` service and generates `FLASK_SECRET_KEY`.
3. In the service's **Environment** tab add:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string from Step 1 |
   | `TMDB_API_KEY` | your TMDB v3 key |
   | `GROQ_API_KEY` | your Groq key *(optional)* |
   | `FRONTEND_ORIGINS` | leave blank for now — add your Vercel URL after step 3 |

4. **Deploy**. Copy the service URL, e.g. `https://nextpick-api.onrender.com`.

**Option B — Manual**

New → **Web Service** → connect repo → Runtime: **Python 3** →  
Build: `pip install -r requirements.txt` →  
Start: `gunicorn web_app:app --workers 1 --threads 8 --worker-class gthread --bind 0.0.0.0:$PORT --timeout 120`

Add the same variables, plus a long random `FLASK_SECRET_KEY`.

Leave **Health Check Path** empty on the free plan. Its 5-second limit is easy to miss on such a small CPU, and each miss makes Render restart a working server.

---

### Step 3 — Deploy the Next.js frontend to Vercel

1. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your GitHub repo.
2. Set **Root Directory** to `frontend`.
3. Add this environment variable:

   | Variable | Value |
   |---|---|
   | `BACKEND_ORIGIN` | your Render URL, e.g. `https://nextpick-api.onrender.com` |

4. **Deploy**. Copy the Vercel URL, e.g. `https://nextpick.vercel.app`.

---

### Step 4 — Wire CORS back to Render

In the Render dashboard → service → **Environment**, set:

```
FRONTEND_ORIGINS = https://nextpick.vercel.app
```

Trigger a redeploy. Your app is live. ✅

---

### Step 5 — Keep the backend awake

Render's free plan sleeps after 15 minutes without traffic, and waking up takes about a minute. A free scheduled ping prevents that:

1. At [cron-job.org](https://cron-job.org), create a job for `https://<your-service>.onrender.com/api/session`.
2. Run it every **5 minutes**.
3. Add the request header `Accept: application/json`. Without it, a sleeping service answers with a wake-up page of over 200 KB, which is above cron-job.org's 64 KB limit, so the job fails.

One always-on service fits inside Render's 750 free hours a month.

---

### Database

The app picks the right database by itself:

| Environment | Database | How |
|---|---|---|
| Local development | SQLite (`data/app.db`) | `DATABASE_URL` not set |
| Live site | PostgreSQL on Neon | `DATABASE_URL` set |

Tables and new columns are created automatically on startup, and existing data is left in place. Neon's free plan suspends the database after 5 minutes without use and wakes it on the next query, without touching your data.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TMDB_API_KEY` | *(required)* | TMDB v3 API key |
| `DATABASE_URL` | *(empty)* | PostgreSQL connection string. Required on Render; leave empty locally to use SQLite |
| `FLASK_SECRET_KEY` | `dev-secret-change-me` | Flask session signing. On Render the app won't start without a strong value |
| `GROQ_API_KEY` | *(optional)* | Enables the chat assistant (Groq) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Chat model — change it if Groq retires the default |
| `GROQ_FALLBACK_MODEL` | `openai/gpt-oss-20b` | Used when the main model is rate-limited or unavailable |
| `BACKEND_ORIGIN` | `http://localhost:5001` | Backend URL used by Next.js rewrites |
| `FRONTEND_ORIGINS` | `http://localhost:3000,...` | Comma-separated CORS allowed origins |
| `TMDB_API_HOST` | `api.themoviedb.org` | Which TMDB hostname to try first. The app switches to the other one (`api.tmdb.org`) by itself when a network blocks the first, so no VPN is needed; setting it only skips the first slow attempt after a restart |
| `TMDB_TIMEOUT_SECONDS` | `5` | Per-request TMDB timeout (`render.yaml` sets 8) |
| `TMDB_MAX_RETRIES` | `2` | TMDB retry count (`render.yaml` sets 3) |
| `TMDB_RETRY_BACKOFF_SECONDS` | `0.75` | Wait between TMDB retries |

Generate a strong secret key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## How Recommendations Work

### Request flow

```
Browser → Next.js (/api/…) → [next.config.mjs rewrite] → Flask
```

All `/api/*` paths are forwarded to Flask by a Next.js rewrite. Cookies pass through because `fetch` always sets `credentials: "include"`.

### Scoring weights (per title)

NextPick uses a hybrid recommender. Rules score how well a title fits your choices, and content-based similarity scores how well it fits your taste.

| Signal | Weight |
|---|---|
| Genre match (per matched genre) | +4.0 |
| Platform match | +3.0 |
| Language match | +2.5 |
| Watch-history genre affinity (+1.0 per match, capped) | 0 – 8.0 |
| Taste similarity to your watch-history profile | 0 – 4.5 |
| TMDB quality (`rating × 0.15`) | 0 – 1.5 |

**Taste similarity:** each title becomes a vector of its genres, language, rating and release year. Your watch history is averaged into a profile vector, and each title scores by its cosine similarity to that profile. Both history signals are zero for a new account, so they only sharpen results as you watch more.

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
  → small-talk check            (answered straight away, no network calls)
  → region's genres & platforms (cached)
  → keyword extraction          (regex, no API)
  → Groq model                  (intent / filters / reference title; keywords are the fallback)
  → TMDB discover or lookup     (filtered catalog)
  → MovieRecommender            (scored & ranked)
  → JSON response
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/session` | Auth state and current user |
| `POST` | `/api/auth/register` | Create account |
| `POST` | `/api/auth/login` | Sign in |
| `POST` | `/api/auth/logout` | Sign out |
| `POST` | `/api/settings/region` | Change region |
| `GET` | `/api/dashboard` | Wizard options and saved recommendations |
| `POST` | `/api/dashboard/recommend` | Filtered recommendations |
| `GET` | `/api/dashboard/today-pick` | Today's Pick |
| `GET` | `/api/dashboard/ott-releases` | Recent streaming arrivals |
| `GET` | `/api/search/trending` | Top searches |
| `GET` | `/api/search/live?query=` | Live search as you type |
| `GET` | `/api/search/suggest?query=` | Search suggestions |
| `POST` | `/api/search/results` | Full search results |
| `GET` | `/api/title/<type>/<id>` | Title detail |
| `GET` | `/api/title/<type>/<id>/similar` | Similar titles |
| `POST` | `/api/title/<type>/<id>/watch` | Toggle watched |
| `POST` | `/api/title/<type>/<id>/watchlist` | Toggle watchlist |
| `POST` `DELETE` | `/api/title/<type>/<id>/rating` | Set or remove a rating (1–10) |
| `GET` | `/api/person/<id>` | Actor or director page |
| `GET` | `/api/history` | Watch history |
| `GET` | `/api/watchlist` | Watchlist |
| `POST` | `/api/chat` | Chat message → AI reply + recs |
| `GET` | `/api/chat/history` | Conversation history |
| `POST` | `/api/chat/clear` | Clear conversation |

---

## Contributing

Pull requests are welcome. Open an issue first for larger changes.

```bash
# Backend — auto-reloads on file changes
python web_app.py

# Frontend — Next.js hot reload
cd frontend && npm run dev
```

Delete `data/app.db` at any time to reset all local user data.

---

## Credits

Movie and TV data and images come from [TMDB](https://www.themoviedb.org/). This product uses the TMDB API but is not endorsed or certified by TMDB.

---

## License

MIT
