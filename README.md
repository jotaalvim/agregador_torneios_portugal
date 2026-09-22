# Xadrez Norte

Chess tournament aggregator for the north of Portugal (Braga, Porto, Viana do
Castelo, and beyond). FastAPI + SQLite backend with a public submission form
and an admin moderation queue.

This replaces the previous static Hakyll site (archived in `legacy-hakyll/`,
kept for reference — it is not used at runtime).

## Stack

- **Backend:** FastAPI, server-rendered with Jinja2 templates (no separate
  frontend build step)
- **Database:** SQLite, single file at `data/chess.db`
- **Auth:** one admin account (env vars), session cookies
- **Deploy:** single Docker container, SQLite file on a named volume

## How it works

- Anyone can submit a tournament at `/submeter`. Submissions land in a
  **pending** queue — they do not appear on the site until an admin approves
  them.
- The admin logs in at `/admin/login` and sees two lists at `/admin`:
  **Pendentes** (new submissions, with approve/reject/edit actions) and
  **Todos os torneios** (every tournament regardless of status, with
  edit/delete). Approving a pending submission is what makes it public;
  rejecting or deleting does not. Admins can also add a tournament directly
  (skipping the pending queue) via **+ Novo torneio** at
  `/admin/tournaments/new`.
- Public pages: `/` (all districts) and `/distrito/{slug}` (one district)
  each have a search box (`?q=`) matching title, location, organizer,
  district, or time control. `/torneios/{id}` shows a single tournament's
  detail (no search), and `/calendario` shows a 12-month calendar grid,
  optionally filtered with `?distrito=`. `.ics` calendar export is
  available per-tournament and combined at `/calendario.ics`.
- `/robots.txt`, `/sitemap.xml`, and `/healthz` (used by the Docker
  healthcheck) are served for crawlers and infra, not meant to be visited
  directly.

## Security notes

- Submissions include a honeypot field; anything that fills it in is
  silently dropped (no error shown, to avoid tipping off bots).
  Regulation/site/results URLs are also validated to start with `http://`
  or `https://`.
- Admin login is rate-limited to 5 attempts per 15 minutes per IP
  (in-memory, resets on restart — fine for the single-process deployment
  this is built for).
- All admin POST actions (create/approve/reject/edit/delete/logout) are
  protected by a per-session CSRF token.

## Run locally (without Docker)

Requires Python 3.12+ (matches the version pinned in the `Dockerfile`;
earlier 3.x versions likely work too but aren't tested).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

ADMIN_USERNAME=admin ADMIN_PASSWORD=change-me SECRET_KEY=dev-only \
  SESSION_HTTPS_ONLY=false \
  .venv/bin/uvicorn app.main:app --reload --port 8000
```

`SESSION_HTTPS_ONLY=false` matters here: unlike Docker Compose (see below),
nothing sets it for you when running this way, and the app itself defaults
to `true` — without it your browser may silently refuse to send the admin
session cookie back over plain HTTP, and login will look broken.

Open <http://localhost:8000>. The database is created automatically at
`data/chess.db` and seeded with the 4 tournaments migrated from the old
Hakyll markdown files (from `app/seed_data.json`), the first time it's
empty. To reset local data, stop the server and delete `data/chess.db` —
it will be recreated and reseeded on the next run.

## Run with Docker

1. Copy `.env.example` to `.env` and fill in real values:

   ```bash
   cp .env.example .env
   # generate a secret key:
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Build and start:

   ```bash
   docker compose up --build -d
   ```

3. Open <http://localhost:8000>.

The SQLite file lives in the `chess_data` named volume (mounted at
`/app/data` in the container), so it survives rebuilds and restarts.
`ADMIN_PASSWORD` and `SECRET_KEY` are **required** — the app refuses to start
without them (no insecure defaults), so it's never accidentally exposed with
dev credentials.

Once you put this behind HTTPS (a reverse proxy like Caddy/Traefik/nginx, or
a platform that terminates TLS for you), set `SESSION_HTTPS_ONLY=true` in
`.env` so the admin session cookie is only ever sent over an encrypted
connection. `docker-compose.yml` defaults it to `false` so the container
also works out of the box over plain HTTP while you're first trying it
out — that default lives in `docker-compose.yml`, not the app itself: if
the variable is unset entirely (e.g. running without Docker, see above),
`app/config.py` defaults to `true`.

## Analytics (self-hosted GoatCounter)

Page-view tracking is optional and off by default. `docker-compose.yml`
includes a [GoatCounter](https://www.goatcounter.com) service. Unlike Umami,
it stores its data in SQLite on its own named volume (`goatcounter_data`) —
no separate database container or password to manage.

`-vhost` (GoatCounter's own term for "where this site's dashboard lives")
must be a real multi-label domain — plain `localhost` is rejected outright
("need at least 2 labels"). For local testing without owning a domain, use
a [nip.io](https://nip.io) address, which publicly resolves straight to an
IP with no `/etc/hosts` editing: `127-0-0-1.nip.io` resolves to `127.0.0.1`.
In production, use a real subdomain you control instead (e.g.
`stats.xadreznorte.pt`), pointed at the server.

**Bringing it up, two ways:**

- **Automatic (recommended for repeat/production deploys):** set
  `GC_SITE_VHOST`, `GC_SITE_ADMIN_EMAIL`, and `GC_SITE_ADMIN_PASSWORD` in
  `.env` before first bringing up `goatcounter`.
  [goatcounter/init.sh](goatcounter/init.sh) creates that site automatically
  on first boot (empty volume) and is a no-op on every later deploy, since
  the site then already exists on `goatcounter_data` — so a redeploy, or
  standing this up fresh on a new server, never needs the manual wizard.
  (These three are deliberately not prefixed `GOATCOUNTER_` — the
  `goatcounter` binary itself validates any `GOATCOUNTER_*` env var as one
  of its own config knobs and refuses to start on an unrecognized one.)
- **Manual (one-off/local):** leave those three blank, start it
  (`docker compose up -d goatcounter`), open <http://localhost:8081> (or
  your nip.io address) and follow the setup wizard.

Either way, once the site exists:

1. In its Settings → Site code, GoatCounter shows you a snippet — but by
   default it assumes a real reverse-proxied HTTPS deployment (`https://`,
   no port). For plain-HTTP/local testing (no reverse proxy in front of
   port 8081), that snippet won't load; use
   `http://<vhost>:8081/count.js` and `http://<vhost>:8081/count` instead.
2. Put those two URLs in `.env` as `GOATCOUNTER_SCRIPT_URL` and
   `GOATCOUNTER_ENDPOINT`, then restart the `web` service
   (`docker compose up -d web`). [base.html](templates/base.html) only
   renders the tracking `<script>` once both are set, so the site behaves
   identically (no script tag at all) until you complete this step.
3. GoatCounter batches pageviews in memory before persisting them, so the
   dashboard's "No data received" banner can take up to a minute to clear
   after your first real visit — that's normal, not a sign it's broken.

In production, put GoatCounter behind HTTPS the same way you would the main
app (reverse proxy or platform TLS) rather than exposing port 8081
directly — it's a login form. The compose service already passes
`-tls http` so GoatCounter itself stays plain HTTP behind that proxy,
matching how `web` is deployed — and once it's behind real HTTPS, the
`https://`-no-port snippet GoatCounter shows you in Settings is the correct
one to use instead of the `:8081` form above.

Once analytics is live, click/event tracking on specific elements (e.g. the
"+ Adicionar torneio" button) works by adding a `data-goatcounter-click="..."`
attribute to that element — GoatCounter picks it up automatically, no extra
script required.

## Logo

The real AXDB (Associação de Xadrez do Distrito de Braga) emblem lives at
`static/logo.jpg`, referenced from `templates/base.html` (header logo and
favicon). To swap it for a different file, drop the new image in `static/`
and update both the `<link rel="icon" ...>` and `<img src="/static/logo...">`
lines in `templates/base.html`.

## Project layout

```text
app/            FastAPI app: routes, models, db, auth, seed data
templates/      Jinja2 templates (public pages + admin)
static/         CSS, logo, uploaded regulation PDFs
tests/          Pytest suite (see Testing below)
data/           SQLite database file (gitignored, created at runtime)
legacy-hakyll/  Archived old Hakyll static-site generator (reference only)
```

## Testing

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Tests spin up the FastAPI app against a throwaway SQLite file (via the
`DATABASE_URL` env var) and cover the public pages, search, the submission
form (including the honeypot), and the admin login/approve flow.

## Known limitations (MVP scope)

- No email notifications when a new tournament is submitted — check
  `/admin` periodically.
- District names are free text (matching the old site's behavior); a typo
  creates a new district instead of matching an existing one. The
  submission form suggests existing districts via autocomplete to reduce
  this.
