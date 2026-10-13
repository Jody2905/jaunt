# Jaunt

A playful travel planner: plan a trip day by day, then book stays, flights, tours and car hire
for it, with real availability, 15-minute holds, cancellation refunds and a budget tracker.

> All businesses, flights and prices are invented demo data. No real payments are taken.

**Status:** Phase 1 of 4 — the database and booking rules. (The API, the front end with its
video header, and PostgreSQL + CI come next.)

## Run it

```
py -m pip install -r requirements-dev.txt
py -m pytest                 # the test suite
py -m jaunt demo             # a printed walkthrough of every rule
py -m jaunt init-demo        # build jaunt.db with Alembic and fill it with demo data
```

Demo logins: `jo@example.test` and `kemar@example.test`, password `Jaunt-2026-demo`.

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

Python 3.11+, SQLAlchemy 2, Alembic, pytest. FastAPI and plain JavaScript in later phases.
