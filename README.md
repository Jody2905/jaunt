# Jaunt

A playful travel planner: plan a trip day by day, then book stays, flights, tours and car hire
for it, with real availability, 15-minute holds, cancellation refunds and a budget tracker.

> All businesses, flights and prices are invented demo data. No real payments are taken.

**Status:** Phase 2 of 4 — booking rules + web API. (The front end with its video header,
then PostgreSQL + CI, come next.)

## Run it

```
py -m pip install -r requirements-dev.txt
py -m pytest                 # the test suite
py -m jaunt demo             # a printed walkthrough of every rule
py -m jaunt init-demo        # build jaunt.db with Alembic and fill it with demo data
py -m jaunt serve            # run the API, then open http://127.0.0.1:8000/docs
```

Demo logins: `jo@example.test` and `kemar@example.test`, password `Jaunt-2026-demo`.

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
- **Session cookie** is random, `HttpOnly` and `SameSite=Lax`; the database stores only its
  SHA-256, so a leaked database can't be used to log in. Sessions last 7 days, logging out
  kills the session server-side, and each login starts a fresh one.
- **CSRF token** required on every change, compared in constant time.
- **Login throttling:** 5 wrong passwords for an email pauses logins for 15 minutes. Wrong
  password and unknown email get the identical answer, and take the same time.
- **Strict input:** unknown fields are rejected and numbers must be real numbers.
- Passwords hashed with scrypt; security headers (`nosniff`, `DENY` framing, `no-store` on API).
- Each protection has a test, and each test was checked by deliberately breaking the protection.

## The rules it enforces

- **Availability is worked out, never stored.** "Rooms left" is counted from bookings that still
  hold stock (confirmed, or held and not yet lapsed), night by night, so it can't drift.
- **Holds.** Picking something sets it aside for 15 minutes. Unconfirmed holds lapse and the stock
  goes back on sale; confirming a lapsed hold is refused.
- **No overbooking under load.** Two people grabbing the last room at once: exactly one gets it
  (row locks on PostgreSQL, `BEGIN IMMEDIATE` on SQLite). There's a threaded test for this.
- **Cancellation policies.** Stays: free until each stay's deadline, then the first night is kept.
  Flights: free within 24 hours of booking. Tours: free until 24 hours before. Cars: free before pick-up.
- **Itinerary.** Every trip day is listed; confirmed bookings add themselves (check-in, flights,
  car pick-up...) and new items slot in by time. Drag-and-drop order is kept after that.
- **Budget.** Booked and on-hold spend against the trip budget.
- **Privacy.** Someone else's trip behaves as if it doesn't exist.
- **The database checks too:** CHECK constraints (e.g. a booking's total must equal price ×
  quantity × nights), unique booking references, and migrations tested against the models.

## Built with

Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, pytest. Plain JavaScript front end in Phase 3.
