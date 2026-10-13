"""Jaunt's web API (FastAPI). Every rule lives in services.py; this file only
translates HTTP to service calls and back.

    py -m jaunt serve            then open http://127.0.0.1:8000/ (the app) or /docs (the API)

Security
  * Logging in sets an HttpOnly, SameSite=Lax cookie holding a random session token.
    The database keeps only a SHA-256 of that token.
  * Every change (POST/PATCH/DELETE) must also send the session's CSRF token in an
    X-CSRF-Token header. Another website can make the browser send the cookie, but
    it can't read the token, so it can't forge a change.
  * Five wrong passwords for one email in 15 minutes pauses logins for that email.
  * Someone else's trip or booking answers 404, exactly as if it didn't exist.
"""

import mimetypes
import secrets
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyCookie, APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import schemas as s
from . import services
from .db import database_url, make_engine, session_factory
from .models import LoginSession, Place, User
from .services import JauntError, NotFound, SoldOut

COOKIE = "jaunt_session"
WEB = Path(__file__).resolve().parent / "web"

# The app's own pages may only load files from this server: no inline scripts, no outside code.
# The /docs page is left out: it loads Swagger UI from a CDN.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
       "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

# Some Windows setups label .js files as text/plain, which browsers refuse to run as modules
for kind, ext in (("text/javascript", ".js"), ("text/css", ".css"), ("video/mp4", ".mp4"), ("font/woff2", ".woff2"),
                  ("video/webm", ".webm"), ("image/svg+xml", ".svg")):
    mimetypes.add_type(kind, ext)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DESCRIPTION = """
Plan a trip day by day and book stays, flights, tours and car hire for it.
**All businesses and prices are invented demo data. No payments are taken.**

### Trying it here
1. Open **POST /api/auth/login**, click *Try it out*, and log in with
   `jo@example.test` / `Jaunt-2026-demo`. Your browser keeps the session cookie.
2. Copy the `csrf_token` from the response, click **Authorize** (top right) and paste it
   into **X-CSRF-Token**. Changes are refused without it.
3. Try **GET /api/trips**, then search, hold and confirm something.

### Booking flow
search → **hold** (stock set aside for 15 minutes) → **confirm** → optionally **cancel**
(the refund follows the booking's cancellation policy; check it first with **GET /refund**).
"""

TAGS = [
    {"name": "account", "description": "Register, log in and out"},
    {"name": "browse", "description": "Places and availability search (no login needed)"},
    {"name": "trips", "description": "Your trips, their day-by-day itinerary and budget"},
    {"name": "bookings", "description": "Hold, confirm and cancel"},
]

session_cookie = APIKeyCookie(name=COOKIE, auto_error=False,
                              description="Set automatically by your browser when you log in")
csrf_header = APIKeyHeader(name="X-CSRF-Token", auto_error=False,
                           description="The csrf_token from login or GET /api/me")


# ======================================================================
# Plumbing: database session, clock, who's logged in
# ======================================================================

def get_db(request: Request):
    db = request.app.state.Session()
    try:
        yield db
    finally:
        db.close()


def get_now(request: Request) -> datetime:
    return request.app.state.clock()


@dataclass
class Auth:
    user: User
    session: LoginSession
    token: str


def signed_in(request: Request, db=Depends(get_db), now=Depends(get_now),
              token: str | None = Security(session_cookie), csrf: str | None = Security(csrf_header)) -> Auth:
    found = services.session_for(db, token, now)
    if found is None:
        raise HTTPException(401, "Please log in")
    user, session = found
    if request.method not in SAFE_METHODS and not (csrf and secrets.compare_digest(csrf, session.csrf_token)):
        raise HTTPException(403, "Missing or wrong CSRF token: send X-CSRF-Token (from GET /api/me)")
    return Auth(user, session, token)


class LoginThrottle:
    """Counts wrong passwords per email. In memory, so it resets on restart (fine for one server)."""
    LIMIT, WINDOW = 5, timedelta(minutes=15)

    def __init__(self):
        self._failures = defaultdict(list)
        self._lock = threading.Lock()

    def wait_seconds(self, email, now):
        with self._lock:
            recent = [t for t in self._failures[email] if t > now - self.WINDOW]
            self._failures[email] = recent
            if len(recent) < self.LIMIT:
                return 0
            return max(1, int((recent[0] + self.WINDOW - now).total_seconds()))

    def failed(self, email, now):
        with self._lock:
            self._failures[email].append(now)

    def succeeded(self, email):
        with self._lock:
            self._failures.pop(email, None)


# ======================================================================
# Turning database rows into responses
# ======================================================================

def booking_out(b, now):
    out = s.BookingOut.model_validate(b)
    if b.status == "held":
        left = int((b.held_until - now).total_seconds())
        if left > 0:
            out.hold_seconds_left = left
        else:
            out.status = "expired"                   # lapsed holds read as expired straight away
    return out


def trip_detail(db, user, trip_id, now):
    trip = services.get_trip(db, user, trip_id)
    days = services.itinerary(db, user, trip.id)
    b = services.budget(db, user, trip.id, now=now)
    return s.TripDetailOut(
        **s.TripOut.model_validate(trip).model_dump(),
        itinerary=[s.DayOut(date=day, items=[s.ItemOut.model_validate(i) for i in items])
                   for day, items in days.items()],
        budget=s.BudgetOut(budget_cents=b.budget_cents, committed_cents=b.committed_cents,
                           on_hold_cents=b.on_hold_cents, remaining_cents=b.remaining_cents,
                           over_budget=b.over_budget),
        bookings=[booking_out(x, now) for x in services.bookings_for_trip(db, user, trip.id)])


def me_out(user, session):
    return s.MeOut(user=s.UserOut.model_validate(user), csrf_token=session.csrf_token)


def set_session_cookie(request, response, token):
    response.set_cookie(COOKIE, token, max_age=services.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.app.state.secure_cookies, path="/")


ERRORS = {400: {"model": s.Problem, "description": "A rule said no"},
          401: {"model": s.Problem, "description": "Not logged in"},
          403: {"model": s.Problem, "description": "Missing CSRF token"},
          404: {"model": s.Problem, "description": "Not found (or not yours)"}}


# ======================================================================
# Account
# ======================================================================

account = APIRouter(prefix="/api", tags=["account"])


@account.post("/auth/register", status_code=201, response_model=s.MeOut, responses={400: ERRORS[400]})
def register(body: s.RegisterIn, request: Request, response: Response, db=Depends(get_db), now=Depends(get_now)):
    """Create an account and log straight in."""
    user = services.create_user(db, body.email, body.name, body.password, now=now)
    token, session = services.start_session(db, user, now=now)
    set_session_cookie(request, response, token)
    return me_out(user, session)


@account.post("/auth/login", response_model=s.MeOut,
              responses={401: ERRORS[401], 429: {"model": s.Problem, "description": "Too many wrong passwords"}})
def login(body: s.LoginIn, request: Request, response: Response, db=Depends(get_db), now=Depends(get_now),
          old_token: str | None = Security(session_cookie)):
    """Log in. Sets the session cookie and returns the CSRF token to send with changes."""
    email = body.email.strip().lower()
    throttle = request.app.state.throttle
    wait = throttle.wait_seconds(email, now)
    if wait:
        raise HTTPException(429, f"Too many wrong passwords. Try again in {wait // 60 + 1} minutes.",
                            headers={"Retry-After": str(wait)})
    user = services.authenticate(db, email, body.password)
    if user is None:
        throttle.failed(email, now)
        raise HTTPException(401, "Wrong email or password")          # same message either way
    throttle.succeeded(email)
    services.end_expired_sessions(db, now)                            # housekeeping
    services.end_session(db, old_token)                               # a fresh session on every login
    token, session = services.start_session(db, user, now=now)
    set_session_cookie(request, response, token)
    return me_out(user, session)


@account.post("/auth/logout", status_code=204, responses={401: ERRORS[401], 403: ERRORS[403]})
def logout(request: Request, response: Response, auth: Auth = Depends(signed_in), db=Depends(get_db)):
    """End this session. The old cookie stops working immediately, even if someone copied it."""
    services.end_session(db, auth.token)
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax",
                           secure=request.app.state.secure_cookies)


@account.get("/me", response_model=s.MeOut, responses={401: ERRORS[401]})
def me(auth: Auth = Depends(signed_in)):
    """Who's logged in, and the CSRF token for this session."""
    return me_out(auth.user, auth.session)


# ======================================================================
# Browsing: places and availability
# ======================================================================

browse = APIRouter(prefix="/api", tags=["browse"])


@browse.get("/places", response_model=list[s.PlaceOut])
def places(db=Depends(get_db)):
    return db.scalars(select(Place).order_by(Place.name)).all()


@browse.get("/search/stays", response_model=list[s.StayOptionOut], responses={400: ERRORS[400]})
def search_stays(place_id: int, check_in: date, check_out: date, rooms: int = Query(1, ge=1, le=5),
                 db=Depends(get_db), now=Depends(get_now)):
    """Rooms with enough free on every night, cheapest first. Prices are for the whole stay."""
    return [s.StayOptionOut(room_type_id=o.room.id, stay=o.room.stay.name, stay_kind=o.room.stay.kind,
                            stars=o.room.stay.stars, room=o.room.name, sleeps=o.room.sleeps, blurb=o.room.stay.blurb,
                            free_cancel_hours=o.room.stay.free_cancel_hours, nightly_cents=o.room.nightly_cents,
                            nights=o.nights, rooms_left=o.rooms_left, total_cents=o.total_cents)
            for o in services.search_stays(db, place_id, check_in, check_out, rooms, now=now)]


@browse.get("/search/flights", response_model=list[s.FlightOptionOut])
def search_flights(origin_id: int, destination_id: int, day: date, passengers: int = Query(1, ge=1, le=9),
                   db=Depends(get_db), now=Depends(get_now)):
    return [s.FlightOptionOut(flight_id=o.flight.id, number=o.flight.number, airline=o.flight.airline,
                              origin=o.flight.origin.code, destination=o.flight.destination.code,
                              departs_at=o.flight.departs_at, arrives_at=o.flight.arrives_at,
                              fare_cents=o.flight.fare_cents, seats_left=o.seats_left, total_cents=o.total_cents)
            for o in services.search_flights(db, origin_id, destination_id, day, passengers, now=now)]


@browse.get("/search/tours", response_model=list[s.TourOptionOut])
def search_tours(place_id: int, day: date, people: int = Query(1, ge=1, le=9),
                 db=Depends(get_db), now=Depends(get_now)):
    return [s.TourOptionOut(slot_id=o.slot.id, tour=o.slot.tour.name, operator=o.slot.tour.operator,
                            blurb=o.slot.tour.blurb, starts_at=o.slot.starts_at, minutes=o.slot.tour.minutes,
                            price_cents=o.slot.tour.price_cents, places_left=o.places_left,
                            total_cents=o.total_cents)
            for o in services.search_tours(db, place_id, day, people, now=now)]


@browse.get("/search/cars", response_model=list[s.CarOptionOut], responses={400: ERRORS[400]})
def search_cars(place_id: int, pickup: date, dropoff: date, cars: int = Query(1, ge=1, le=3),
                db=Depends(get_db), now=Depends(get_now)):
    return [s.CarOptionOut(car_class_id=o.car.id, company=o.car.company, name=o.car.name, seats=o.car.seats,
                           daily_cents=o.car.daily_cents, days=o.days, cars_left=o.cars_left,
                           total_cents=o.total_cents)
            for o in services.search_cars(db, place_id, pickup, dropoff, cars, now=now)]


# ======================================================================
# Trips and the itinerary
# ======================================================================

trips = APIRouter(prefix="/api", tags=["trips"], responses={k: ERRORS[k] for k in (401, 403, 404)})


@trips.get("/trips", response_model=list[s.TripOut])
def list_trips(auth: Auth = Depends(signed_in), db=Depends(get_db)):
    return services.trips_for(db, auth.user)


@trips.post("/trips", status_code=201, response_model=s.TripDetailOut, responses={400: ERRORS[400]})
def create_trip(body: s.TripIn, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    trip = services.create_trip(db, auth.user, body.title, body.place_id, body.start_date, body.end_date,
                                body.travellers, body.budget_cents, now=now)
    return trip_detail(db, auth.user, trip.id, now)


@trips.get("/trips/{trip_id}", response_model=s.TripDetailOut)
def get_trip(trip_id: int, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    """The trip with every day of its itinerary, its budget and its bookings."""
    return trip_detail(db, auth.user, trip_id, now)


@trips.patch("/trips/{trip_id}", response_model=s.TripDetailOut, responses={400: ERRORS[400]})
def update_trip(trip_id: int, body: s.TripPatch, auth: Auth = Depends(signed_in), db=Depends(get_db),
                now=Depends(get_now)):
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    services.update_trip(db, auth.user, trip_id, now=now, **changes)
    return trip_detail(db, auth.user, trip_id, now)


@trips.delete("/trips/{trip_id}", status_code=204, responses={400: ERRORS[400]})
def delete_trip(trip_id: int, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    services.delete_trip(db, auth.user, trip_id, now=now)


@trips.post("/trips/{trip_id}/items", status_code=201, response_model=s.TripDetailOut,
            responses={400: ERRORS[400]})
def add_item(trip_id: int, body: s.ItemIn, auth: Auth = Depends(signed_in), db=Depends(get_db),
             now=Depends(get_now)):
    """Add something to a day. Timed items slot in by time; the updated trip comes back."""
    services.add_item(db, auth.user, trip_id, body.day, body.title, kind=body.kind, time_text=body.time,
                      notes=body.notes)
    return trip_detail(db, auth.user, trip_id, now)


@trips.patch("/items/{item_id}", response_model=s.TripDetailOut, responses={400: ERRORS[400]})
def update_item(item_id: int, body: s.ItemPatch, auth: Auth = Depends(signed_in), db=Depends(get_db),
                now=Depends(get_now)):
    changes = body.model_dump(exclude_unset=True)
    if "time" in changes:
        changes["time_text"] = changes.pop("time")
    item = services.update_item(db, auth.user, item_id, **changes)
    return trip_detail(db, auth.user, item.trip_id, now)


@trips.post("/items/{item_id}/move", response_model=s.TripDetailOut, responses={400: ERRORS[400]})
def move_item(item_id: int, body: s.MoveIn, auth: Auth = Depends(signed_in), db=Depends(get_db),
              now=Depends(get_now)):
    """Drag and drop: put the item at `position` (0 = first) on `day`."""
    item = services.move_item(db, auth.user, item_id, body.day, body.position)
    return trip_detail(db, auth.user, item.trip_id, now)


@trips.delete("/items/{item_id}", response_model=s.TripDetailOut, responses={400: ERRORS[400]})
def delete_item(item_id: int, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    trip_id = services.delete_item(db, auth.user, item_id)
    return trip_detail(db, auth.user, trip_id, now)


# ======================================================================
# Bookings
# ======================================================================

bookings = APIRouter(prefix="/api", tags=["bookings"],
                     responses={**{k: ERRORS[k] for k in (400, 401, 403, 404)},
                                409: {"model": s.Problem, "description": "Not enough left"}})


@bookings.post("/trips/{trip_id}/holds/stay", status_code=201, response_model=s.BookingOut)
def hold_stay(trip_id: int, body: s.StayHoldIn, auth: Auth = Depends(signed_in), db=Depends(get_db),
              now=Depends(get_now)):
    """Set rooms aside for 15 minutes while you decide."""
    return booking_out(services.hold_stay(db, auth.user, trip_id, body.room_type_id, body.check_in,
                                          body.check_out, body.rooms, now=now), now)


@bookings.post("/trips/{trip_id}/holds/flight", status_code=201, response_model=s.BookingOut)
def hold_flight(trip_id: int, body: s.FlightHoldIn, auth: Auth = Depends(signed_in), db=Depends(get_db),
                now=Depends(get_now)):
    return booking_out(services.hold_flight(db, auth.user, trip_id, body.flight_id, body.passengers, now=now), now)


@bookings.post("/trips/{trip_id}/holds/tour", status_code=201, response_model=s.BookingOut)
def hold_tour(trip_id: int, body: s.TourHoldIn, auth: Auth = Depends(signed_in), db=Depends(get_db),
              now=Depends(get_now)):
    return booking_out(services.hold_tour(db, auth.user, trip_id, body.slot_id, body.people, now=now), now)


@bookings.post("/trips/{trip_id}/holds/car", status_code=201, response_model=s.BookingOut)
def hold_car(trip_id: int, body: s.CarHoldIn, auth: Auth = Depends(signed_in), db=Depends(get_db),
             now=Depends(get_now)):
    return booking_out(services.hold_car(db, auth.user, trip_id, body.car_class_id, body.pickup, body.dropoff,
                                         body.cars, now=now), now)


@bookings.post("/bookings/{booking_id}/confirm", response_model=s.BookingOut)
def confirm(booking_id: int, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    """Confirm a hold (a real system would take payment here). It appears in the itinerary."""
    return booking_out(services.confirm(db, auth.user, booking_id, now=now), now)


@bookings.get("/bookings/{booking_id}/refund", response_model=s.RefundOut)
def refund_quote(booking_id: int, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    """What cancelling right now would refund, and why. Changes nothing."""
    r = services.refund_quote(db, auth.user, booking_id, now=now)
    return s.RefundOut(refund_cents=r.cents, reason=r.reason)


@bookings.post("/bookings/{booking_id}/cancel", response_model=s.CancelOut)
def cancel(booking_id: int, auth: Auth = Depends(signed_in), db=Depends(get_db), now=Depends(get_now)):
    b, r = services.cancel(db, auth.user, booking_id, now=now)
    return s.CancelOut(booking=booking_out(b, now), refund_cents=b.refund_cents, reason=r.reason)


# ======================================================================
# The app
# ======================================================================

def create_app(url=None, engine=None, clock=None, secure_cookies=False):
    """`clock` lets tests choose what time it is. Pass secure_cookies=True when served over HTTPS."""
    app = FastAPI(title="Jaunt", version="1.0.0", description=DESCRIPTION, openapi_tags=TAGS)
    app.state.Session = session_factory(engine or make_engine(url or database_url()))
    app.state.clock = clock or datetime.now
    app.state.secure_cookies = secure_cookies
    app.state.throttle = LoginThrottle()

    for router in (account, browse, trips, bookings):
        app.include_router(router)

    @app.exception_handler(JauntError)
    def rule_broken(request, exc):
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, SoldOut) else 400
        return JSONResponse({"detail": str(exc)}, status_code=status)

    @app.exception_handler(RequestValidationError)
    def bad_request(request, exc):
        def where(err):
            return ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
        return JSONResponse({"detail": "; ".join(f"{where(e)}: {e['msg']}" for e in exc.errors())},
                            status_code=422)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        path = request.url.path
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if not (path.startswith("/docs") or path.startswith("/redoc")):
            response.headers["Content-Security-Policy"] = CSP
        return response

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def home():
        """The whole front end is one page; it moves between screens itself (#/trips, #/trips/1...)."""
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    return app
