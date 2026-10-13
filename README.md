# Jaunt

[![CI](https://github.com/Jody2905/jaunt/actions/workflows/ci.yml/badge.svg)](https://github.com/Jody2905/jaunt/actions/workflows/ci.yml)

A playful travel planner: plan a Caribbean island trip day by day, then book stays, flights,
tours and car hire for it, with real availability, 15-minute holds, cancellation refunds and a
budget tracker.

> All businesses, flights and prices are invented demo data. No real payments are taken.

![Jaunt's home page: a painted, looping island scene with the headline "Go on, take a jaunt."](docs/screenshot-home.jpg)

| Planning a trip | On a phone |
|---|---|
| ![A trip's day-by-day plan, budget meter and a tour on a 15-minute hold](docs/screenshot-trip.jpg) | ![The home page on a phone](docs/screenshot-phone.jpg) |

## Run it

Needs Python 3.11 or newer. On Windows use `py`; on macOS/Linux use `python3`.

```
py -m pip install -r requirements-dev.txt
py -m pytest                 # the test suite
py -m jaunt demo             # a printed walkthrough of every rule
py -m jaunt init-demo        # build jaunt.db with Alembic and fill it with demo data
py -m jaunt serve            # then open http://127.0.0.1:8000/  (API docs at /docs)
```

Demo logins: `jo@example.test` and `kemar@example.test`, password `Jaunt-2026-demo`.
The login page also has a one-click "Log in as Jo" button.

### On PostgreSQL

SQLite is the default. To use PostgreSQL instead, create an empty database and point Jaunt at it:

```
$env:JAUNT_DATABASE_URL = "postgresql+psycopg://user:password@localhost/jaunt"    # PowerShell
py -m jaunt init-demo
py -m jaunt serve
```

To run the tests against PostgreSQL, set `JAUNT_TEST_DATABASE_URL` the same way
(the tests empty that database first, so give them one of their own).

## The rules it enforces

- **Availability is worked out, never stored.** "Rooms left" is counted from bookings that still
  hold stock (confirmed, or held and not yet lapsed), night by night, so it can't drift.
- **Holds.** Picking something sets it aside for 15 minutes. Unconfirmed holds lapse and the stock
  goes back on sale; confirming a lapsed hold is refused.
- **One confirmed stay a night.** You can hold several places to stay for the same nights while
  you compare, but only one can be confirmed for any night (a group books "2 rooms" in one
  booking). The page says so before you try.
- **No double booking under load.** When two people grab the last room at the same instant,
  exactly one gets it. When two tabs confirm overlapping stays at once, exactly one wins. Row
  locks do this on PostgreSQL and `BEGIN IMMEDIATE` on SQLite, and threaded tests force the
  worst timing on purpose.
- **Cancellation policies.** Stays: free until each stay's deadline, then the first night is kept.
  Flights: free within 24 hours of booking. Tours: free until 24 hours before. Cars: free before pick-up.
- **Itinerary.** Every trip day is listed. Confirmed bookings add themselves (check-in, flights,
  car pick-up), and new items slot in by time. Drag-and-drop order is kept after that.
- **Budget.** Booked and on-hold spend against the trip budget.
- **Privacy.** Someone else's trip behaves as if it doesn't exist.
- **The database checks too:** CHECK constraints (e.g. a booking's total must equal price ×
  quantity × nights), unique booking references, and a test that the migrations build exactly
  what the models describe.

## The front end

Plain JavaScript (no framework, no build step) talking to the JSON API.

- **Video header.** A painted island scene drawn frame by frame in Python and encoded with
  ffmpeg ([tools/make_hero_video.py](tools/make_hero_video.py)). It lasts 10 seconds and every
  movement completes a whole number of cycles, so it loops with no jump, in about 340 KB. It is
  muted, has a pause button, and doesn't play at all for people who've asked their system for
  reduced motion.
- **Day-by-day plan** with drag and drop between days, plus up/down buttons that do the same
  for keyboards and touch screens.
- **Booking panel** for stays, flights, tours and cars, with a live 15-minute hold countdown,
  a confirm step, and the refund quote shown before you cancel.
- **Budget meter**: booked and on-hold spend against the trip budget.
- **Safe by construction**: every piece of text goes onto the page with `textContent`, never
  as HTML, and a strict Content Security Policy allows only the app's own files (the font is
  self-hosted). A test checks the script never uses `innerHTML`.
- Works from 360px phones up; visible keyboard focus; colours chosen for contrast.

## The API

Interactive docs at `/docs`. Log in with **POST /api/auth/login**, copy the `csrf_token`
from the response into **Authorize → X-CSRF-Token**, then try anything.

| Area | Endpoints |
|---|---|
| Account | `POST /api/auth/register`, `/login`, `/logout`, `GET /api/me` |
| Browse (no login) | `GET /api/places`, `/api/search/stays`, `/flights`, `/tours`, `/cars` |
| Trips | `GET/POST /api/trips`, `GET/PATCH/DELETE /api/trips/{id}` (with itinerary, budget, bookings) |
| Itinerary | `POST /api/trips/{id}/items`, `PATCH/DELETE /api/items/{id}`, `POST /api/items/{id}/move` |
| Bookings | `POST /api/trips/{id}/holds/{stay,flight,tour,car}`, `POST /api/bookings/{id}/confirm`, `GET .../refund`, `POST .../cancel` |

Errors always come back as `{"detail": "a message you can show the traveller"}`:
400 a rule said no, 401 not logged in, 403 missing CSRF token, 404 not found (or not yours),
409 sold out, 422 malformed input, 429 too many wrong passwords.

### Security

- **Session cookie** is random, `HttpOnly` and `SameSite=Lax`. The database stores only its
  SHA-256, so a leaked database can't be used to log in. Sessions last 7 days, logging out
  ends the session on the server, and each login starts a fresh one.
- **CSRF token** required on every change, compared in constant time.
- **Login throttling:** 5 wrong passwords for an email pauses logins for 15 minutes. A wrong
  password and an unknown email get the identical answer and take the same time.
- **Strict input:** unknown fields are rejected and numbers must be real numbers.
- Passwords hashed with scrypt. Security headers on every response (`nosniff`, no framing,
  `no-store` on the API, a Content Security Policy on the app's pages).
- Each protection has a test, and each test was checked by deliberately breaking the
  protection and watching it fail.

## How it's organised

```
jaunt/
  models.py      the tables (SQLAlchemy 2) and their CHECK constraints
  services.py    every rule: availability, holds, confirming, refunds, the itinerary, sessions
  api.py         FastAPI: turns HTTP into service calls; security headers, CSRF, throttling
  schemas.py     the shapes of API requests and responses (Pydantic 2, strict)
  catalogue.py   the invented islands, stays, flights, tours and cars
  web/           the front end: index.html, app.js, app.css, the font and the header video
migrations/      Alembic migrations (the real way the database is built)
tests/           pytest: rules, API, races, migrations, front end and a real-browser run
tools/           the script that draws the header video
```

The rules live in one place, `services.py`. The API, the command line and the tests all call
the same functions, so a rule can't be enforced in one place and forgotten in another.

## Tests and CI

Every push runs six jobs on GitHub Actions:

- **lint:** ruff
- **tests on Python 3.11, 3.12 and 3.13:** SQLite, plus the printed walkthrough
- **PostgreSQL 16:** the full suite, then building the demo database through the migrations
- **browser:** Playwright drives Chromium through planning, holding and confirming

On a laptop without Playwright the browser test is skipped. To run it, use
`pip install playwright` then `playwright install chromium`.

## Built with

Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL / SQLite, pytest,
Playwright, ruff, GitHub Actions; plain JavaScript and CSS; ffmpeg for the header video.
Font: Bricolage Grotesque (SIL Open Font Licence).
