"""Jaunt's rules. The API in Phase 2 only calls these functions.

Every function takes a database session `db` and, where time matters, `now`
(so tests can say exactly what time it is). Functions that change data commit
their own transaction: either everything happens or nothing does.

Booking flow
    search  ->  hold (stock set aside for 15 minutes)  ->  confirm
                  |                                          |
                  +-> expires if not confirmed               +-> cancel (refund depends on the policy)

Cancellation policies
    stay    free until `free_cancel_hours` before check-in (48 by default); after that the first night is kept
    flight  free within 24 hours of confirming; after that the fare is non-refundable
    tour    free until 24 hours before it starts; after that non-refundable
    car     free any time before pick-up
"""

import hashlib
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import func, or_, select

from . import passwords
from .models import (
    Booking,
    CarClass,
    Flight,
    ItineraryItem,
    LoginSession,
    Place,
    RoomType,
    Stay,
    Tour,
    TourSlot,
    Trip,
    User,
)
from .money import fmt

HOLD_MINUTES = 15
MAX_TRIP_DAYS = 30
CHECK_IN, CHECK_OUT = time(14, 0), time(11, 0)
CAR_PICKUP, CAR_DROPOFF = time(9, 0), time(17, 0)


@contextmanager
def _tx(db):
    """One unit of work: commit if everything succeeded, roll back if anything failed."""
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


class JauntError(Exception):
    """A rule said no. The message is safe to show to the traveller."""


class NotFound(JauntError):
    pass


class SoldOut(JauntError):
    """Not enough left. (The API answers 409 Conflict for this one.)"""


# ======================================================================
# People
# ======================================================================

def create_user(db, email, name, password, now=None):
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 254:
        raise JauntError("Please enter a valid email address")
    if not (name or "").strip():
        raise JauntError("Please enter your name")
    try:
        hashed = passwords.hash_password(password)
    except ValueError as e:
        raise JauntError(str(e)) from None
    with _tx(db):
        if db.scalar(select(User).where(User.email == email)):
            raise JauntError("There's already an account with this email")
        user = User(email=email, name=name.strip()[:80], password_hash=hashed, created_at=now or datetime.now())
        db.add(user)
    return user


def authenticate(db, email, password):
    user = db.scalar(select(User).where(User.email == (email or "").strip().lower()))
    db.rollback()                                   # end the read transaction straight away
    # Hash something even for unknown emails, so timing doesn't reveal which emails have accounts
    stored = user.password_hash if user else passwords.hash_password("timing-equaliser")
    return user if passwords.verify_password(password or "", stored) and user else None


# ======================================================================
# Login sessions
# ======================================================================
# The browser keeps a random token in a cookie. The database stores only a SHA-256
# of it, so a leaked copy of the database can't be used to log in as anyone.
# Each session also has its own CSRF token, which the page must send back as a
# header on every change (a forged form on another website can't read it).

SESSION_DAYS = 7


def start_session(db, user, now=None):
    """Returns (cookie token, session). The token is shown once and never stored."""
    now = now or datetime.now()
    token = secrets.token_urlsafe(32)
    with _tx(db):
        session = LoginSession(token_hash=_hash_token(token), user_id=user.id, csrf_token=secrets.token_urlsafe(32),
                               created_at=now, expires_at=now + timedelta(days=SESSION_DAYS))
        db.add(session)
    return token, session


def session_for(db, token, now=None):
    """(user, session) for a cookie token, or None if it's unknown or has run out."""
    if not token:
        return None
    now = now or datetime.now()
    row = db.execute(select(LoginSession, User).join(User, User.id == LoginSession.user_id)
                     .where(LoginSession.token_hash == _hash_token(token))).first()
    if row is None or row[0].expires_at <= now:
        db.rollback()
        return None
    session, user = row
    db.expunge(session)
    db.expunge(user)                                # keep their loaded values after the read ends
    db.rollback()
    return user, session


def end_session(db, token):
    if token:
        with _tx(db):
            db.execute(LoginSession.__table__.delete().where(LoginSession.token_hash == _hash_token(token)))


def end_expired_sessions(db, now=None):
    with _tx(db):
        return db.execute(LoginSession.__table__.delete().where(
            LoginSession.expires_at <= (now or datetime.now()))).rowcount


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ======================================================================
# Trips and the itinerary
# ======================================================================

def create_trip(db, user, title, place_id, start, end, travellers=1, budget_cents=0, now=None):
    now = now or datetime.now()
    with _tx(db):
        place = _get(db, Place, place_id)
        _check_trip_fields(title, start, end, travellers, budget_cents, now)
        trip = Trip(owner_id=user.id, title=title.strip(), place_id=place.id, start_date=start, end_date=end,
                    travellers=travellers, budget_cents=budget_cents, created_at=now)
        db.add(trip)
    return trip


def update_trip(db, user, trip_id, now=None, **changes):
    """Change title, dates, travellers or budget. Dates can't shrink past anything already planned."""
    now = now or datetime.now()
    with _tx(db):
        trip = _own_trip(db, user, trip_id, lock=True)
        new = {k: changes.get(k, getattr(trip, k)) for k in ("title", "start_date", "end_date", "travellers",
                                                              "budget_cents")}
        if (new["start_date"], new["end_date"]) != (trip.start_date, trip.end_date):
            _check_trip_fields(new["title"], new["start_date"], new["end_date"], new["travellers"],
                               new["budget_cents"], now)
            outside = db.scalar(select(func.count()).select_from(ItineraryItem).where(
                ItineraryItem.trip_id == trip.id,
                or_(ItineraryItem.day < new["start_date"], ItineraryItem.day > new["end_date"])))
            if outside:
                raise JauntError(f"{outside} plan item(s) fall outside the new dates. Move or remove them first.")
        else:
            _check_trip_fields(new["title"], trip.start_date, trip.end_date, new["travellers"],
                               new["budget_cents"], now, allow_past=True)
        booked = db.scalar(select(func.max(Booking.quantity)).where(
            Booking.trip_id == trip.id, Booking.kind.in_(("flight", "tour")), _holding_stock(now)))
        if booked and new["travellers"] < booked:
            raise JauntError(f"You have bookings for {booked} people, so travellers can't go below that")
        for k, v in new.items():
            setattr(trip, k, v.strip() if k == "title" else v)
    return trip


def delete_trip(db, user, trip_id, now=None):
    now = now or datetime.now()
    with _tx(db):
        trip = _own_trip(db, user, trip_id, lock=True)
        if db.scalar(select(func.count()).select_from(Booking).where(Booking.trip_id == trip.id,
                                                                     _holding_stock(now))):
            raise JauntError("Cancel this trip's bookings before deleting it")
        db.execute(ItineraryItem.__table__.delete().where(ItineraryItem.trip_id == trip.id))
        db.execute(Booking.__table__.delete().where(Booking.trip_id == trip.id))
        db.delete(trip)


def trips_for(db, user):
    return db.scalars(select(Trip).where(Trip.owner_id == user.id).order_by(Trip.start_date)).all()


def get_trip(db, user, trip_id):
    return _own_trip(db, user, trip_id)


def add_item(db, user, trip_id, day, title, kind="activity", time_text=None, notes=""):
    with _tx(db):
        trip = _own_trip(db, user, trip_id, lock=True)
        if kind not in ("activity", "food", "transport", "note"):
            raise JauntError("Choose activity, food, transport or note")
        _check_day(trip, day)
        item = ItineraryItem(trip_id=trip.id, day=day, kind=kind, title=_title(title),
                             time=_time_text(time_text), notes=(notes or "").strip()[:2000])
        _slot_in(db, item)
    return item


def update_item(db, user, item_id, **changes):
    with _tx(db):
        item, trip = _own_item(db, user, item_id)
        if item.kind == "booking" and set(changes) - {"notes"}:
            raise JauntError("Booked items change through the booking itself; you can only edit their notes")
        if "title" in changes:
            item.title = _title(changes["title"])
        if "time_text" in changes:
            item.time = _time_text(changes["time_text"])
        if "notes" in changes:
            item.notes = (changes["notes"] or "").strip()[:2000]
        if "kind" in changes:
            if changes["kind"] not in ("activity", "food", "transport", "note"):
                raise JauntError("Choose activity, food, transport or note")
            item.kind = changes["kind"]
    return item


def move_item(db, user, item_id, to_day, to_position):
    """Drag-and-drop: put the item at `to_position` on `to_day` and renumber both days 0, 1, 2..."""
    with _tx(db):
        item, trip = _own_item(db, user, item_id, lock_trip=True)
        _check_day(trip, to_day)
        if item.kind == "booking" and to_day != item.day:
            raise JauntError("A booking stays on its own day; you can reorder it within the day")
        from_day = item.day
        source = [i for i in _day_items(db, trip.id, from_day) if i.id != item.id]
        target = source if to_day == from_day else _day_items(db, trip.id, to_day)
        to_position = max(0, min(int(to_position), len(target)))
        target.insert(to_position, item)
        item.day = to_day
        for pos, i in enumerate(target):
            i.position = pos
        if to_day != from_day:
            for pos, i in enumerate(source):
                i.position = pos
    return item


def delete_item(db, user, item_id):
    """Remove an item and close the gap. Returns the trip's id."""
    with _tx(db):
        item, trip = _own_item(db, user, item_id, lock_trip=True)
        if item.kind == "booking":
            raise JauntError("Cancel the booking to remove it from your plan")
        day = item.day
        db.delete(item)
        db.flush()
        for pos, i in enumerate(_day_items(db, trip.id, day)):
            i.position = pos
    return trip.id


def itinerary(db, user, trip_id):
    """{day: [items in order]} for every day of the trip, including empty days."""
    trip = _own_trip(db, user, trip_id)
    items = db.scalars(select(ItineraryItem).where(ItineraryItem.trip_id == trip.id)
                       .order_by(ItineraryItem.day, ItineraryItem.position)).all()
    days = {trip.start_date + timedelta(days=n): [] for n in range(trip.days)}
    for item in items:
        days.setdefault(item.day, []).append(item)
    return days


@dataclass
class Budget:
    budget_cents: int
    committed_cents: int            # confirmed bookings, plus whatever was kept on cancelled ones
    on_hold_cents: int              # held but not confirmed yet

    @property
    def remaining_cents(self):
        return self.budget_cents - self.committed_cents - self.on_hold_cents

    @property
    def over_budget(self):
        return self.budget_cents > 0 and self.remaining_cents < 0


def budget(db, user, trip_id, now=None):
    now = now or datetime.now()
    trip = _own_trip(db, user, trip_id)
    committed = held = 0
    for b in db.scalars(select(Booking).where(Booking.trip_id == trip.id)):
        if b.status == "confirmed":
            committed += b.total_cents
        elif b.status == "cancelled" and b.confirmed_at:
            committed += b.total_cents - b.refund_cents
        elif b.status == "held" and b.held_until > now:
            held += b.total_cents
    return Budget(trip.budget_cents, committed, held)


# ======================================================================
# Searching availability
# ======================================================================

@dataclass
class StayOption:
    room: RoomType
    rooms_left: int
    nights: int
    total_cents: int


@dataclass
class FlightOption:
    flight: Flight
    seats_left: int
    total_cents: int


@dataclass
class TourOption:
    slot: TourSlot
    places_left: int
    total_cents: int


@dataclass
class CarOption:
    car: CarClass
    cars_left: int
    days: int
    total_cents: int


def search_stays(db, place_id, check_in, check_out, rooms=1, now=None):
    now = now or datetime.now()
    nights = _nights(check_in, check_out)
    options = []
    for room in db.scalars(select(RoomType).join(Stay).where(Stay.place_id == place_id)
                           .order_by(RoomType.nightly_cents)):
        left = _rooms_left(db, room, check_in, check_out, now)
        if left >= rooms:
            options.append(StayOption(room, left, nights, room.nightly_cents * nights * rooms))
    return options


def search_flights(db, origin_id, destination_id, day, passengers=1, now=None):
    now = now or datetime.now()
    start = datetime.combine(day, time.min)
    options = []
    for f in db.scalars(select(Flight).where(Flight.origin_id == origin_id, Flight.destination_id == destination_id,
                                             Flight.departs_at >= max(start, now),
                                             Flight.departs_at < start + timedelta(days=1))
                        .order_by(Flight.departs_at)):
        left = _seats_left(db, f, now)
        if left >= passengers:
            options.append(FlightOption(f, left, f.fare_cents * passengers))
    return options


def search_tours(db, place_id, day, people=1, now=None):
    now = now or datetime.now()
    start = datetime.combine(day, time.min)
    options = []
    for s in db.scalars(select(TourSlot).join(Tour).where(Tour.place_id == place_id,
                                                          TourSlot.starts_at >= max(start, now),
                                                          TourSlot.starts_at < start + timedelta(days=1))
                        .order_by(TourSlot.starts_at)):
        left = _places_left(db, s, now)
        if left >= people:
            options.append(TourOption(s, left, s.tour.price_cents * people))
    return options


def search_cars(db, place_id, pickup, dropoff, cars=1, now=None):
    now = now or datetime.now()
    days = _car_days(pickup, dropoff)
    options = []
    for c in db.scalars(select(CarClass).where(CarClass.place_id == place_id).order_by(CarClass.daily_cents)):
        left = _cars_left(db, c, pickup, dropoff, now)
        if left >= cars:
            options.append(CarOption(c, left, days, c.daily_cents * days * cars))
    return options


# ======================================================================
# Holding, confirming and cancelling
# ======================================================================

def hold_stay(db, user, trip_id, room_type_id, check_in, check_out, rooms=1, now=None):
    now = now or datetime.now()
    with _tx(db):
        trip = _own_trip(db, user, trip_id)
        room = _get(db, RoomType, room_type_id, lock=True)        # one booking at a time for this room type
        nights = _nights(check_in, check_out)
        _within_trip(trip, check_in, check_out)
        _quantity(rooms, 5, "rooms")
        left = _rooms_left(db, room, check_in, check_out, now)
        if left < rooms:
            raise SoldOut(_sold_out(left, "room", f"{room.stay.name} ({room.name})"))
        return _new_booking(db, trip, user, "stay", now, room_type_id=room.id,
                            title=f"{room.stay.name}, {room.name}", start_date=check_in, end_date=check_out,
                            starts_at=datetime.combine(check_in, CHECK_IN), quantity=rooms, units=nights,
                            unit_cents=room.nightly_cents)


def hold_flight(db, user, trip_id, flight_id, passengers=1, now=None):
    now = now or datetime.now()
    with _tx(db):
        trip = _own_trip(db, user, trip_id)
        flight = _get(db, Flight, flight_id, lock=True)
        _within_trip(trip, flight.departs_at.date(), flight.departs_at.date())
        _quantity(passengers, trip.travellers, "passengers", f"(this trip is for {trip.travellers})")
        if flight.departs_at <= now:
            raise JauntError("That flight has already left")
        left = _seats_left(db, flight, now)
        if left < passengers:
            raise SoldOut(_sold_out(left, "seat", f"flight {flight.number}"))
        return _new_booking(db, trip, user, "flight", now, flight_id=flight.id,
                            title=f"Flight {flight.number} {flight.origin.code}→{flight.destination.code}",
                            start_date=flight.departs_at.date(), end_date=flight.departs_at.date(),
                            starts_at=flight.departs_at, quantity=passengers, units=1, unit_cents=flight.fare_cents)


def hold_tour(db, user, trip_id, slot_id, people=1, now=None):
    now = now or datetime.now()
    with _tx(db):
        trip = _own_trip(db, user, trip_id)
        slot = _get(db, TourSlot, slot_id, lock=True)
        _within_trip(trip, slot.starts_at.date(), slot.starts_at.date())
        _quantity(people, trip.travellers, "people", f"(this trip is for {trip.travellers})")
        if slot.starts_at <= now:
            raise JauntError("That tour has already started")
        left = _places_left(db, slot, now)
        if left < people:
            raise SoldOut(_sold_out(left, "place", slot.tour.name))
        return _new_booking(db, trip, user, "tour", now, tour_slot_id=slot.id, title=slot.tour.name,
                            start_date=slot.starts_at.date(), end_date=slot.starts_at.date(),
                            starts_at=slot.starts_at, quantity=people, units=1, unit_cents=slot.tour.price_cents)


def hold_car(db, user, trip_id, car_class_id, pickup, dropoff, cars=1, now=None):
    now = now or datetime.now()
    with _tx(db):
        trip = _own_trip(db, user, trip_id)
        car = _get(db, CarClass, car_class_id, lock=True)
        days = _car_days(pickup, dropoff)
        _within_trip(trip, pickup, dropoff)
        _quantity(cars, 3, "cars")
        left = _cars_left(db, car, pickup, dropoff, now)
        if left < cars:
            raise SoldOut(_sold_out(left, "car", f"{car.company} {car.name}"))
        return _new_booking(db, trip, user, "car", now, car_class_id=car.id, title=f"{car.company} {car.name}",
                            start_date=pickup, end_date=dropoff, starts_at=datetime.combine(pickup, CAR_PICKUP),
                            quantity=cars, units=days, unit_cents=car.daily_cents)


def confirm(db, user, booking_id, now=None):
    """Confirm a hold. (In a real system this is where payment would be taken.)

    A trip can hold several places to stay for the same nights while the traveller
    compares them, but only one can be confirmed for any night. (A group that needs
    two rooms books "2 rooms" in one booking.)
    """
    now = now or datetime.now()
    with _tx(db):
        b = _own_booking(db, user, booking_id, lock=True)
        lapsed = b.status == "held" and b.held_until <= now
        if lapsed:
            b.status = "expired"                     # saved, then reported below
        elif b.status != "held":
            raise JauntError(f"This booking is {b.status}, so it can't be confirmed")
        else:
            if b.kind == "stay":
                _one_stay_a_night(db, user, b)
            b.status, b.confirmed_at = "confirmed", now
            _add_booking_to_plan(db, b)
    if lapsed:
        raise JauntError("This hold has expired, so the space was released. Please search again.")
    return b


def _one_stay_a_night(db, user, b):
    _own_trip(db, user, b.trip_id, lock=True)     # one confirmation at a time per trip, so two can't race
    clash = db.scalar(select(Booking).where(
        Booking.trip_id == b.trip_id, Booking.kind == "stay", Booking.status == "confirmed", Booking.id != b.id,
        Booking.start_date < b.end_date, Booking.end_date > b.start_date).order_by(Booking.start_date))
    if clash:
        first, last = max(b.start_date, clash.start_date), min(b.end_date, clash.end_date) - timedelta(days=1)
        nights = (f"the night of {first:%a %d %b}" if first == last
                  else f"the nights of {first:%a %d %b} to {last:%a %d %b}")
        raise JauntError(f"You already have {clash.title} confirmed for {nights}. A trip can have one confirmed "
                         f"stay a night: cancel that one first, or book extra rooms in a single booking.")


@dataclass
class Refund:
    cents: int
    reason: str


def refund_for(b, now, free_cancel_hours=48):
    """What a cancellation now would refund, and why. Doesn't change anything."""
    if b.status == "held":
        return Refund(0, "Nothing was charged for a hold")
    if b.status != "confirmed":
        raise JauntError(f"This booking is {b.status}")
    if now >= b.starts_at:
        raise JauntError("This has already started, so it can't be cancelled")
    if b.kind == "stay":
        if now <= b.starts_at - timedelta(hours=free_cancel_hours):
            return Refund(b.total_cents, f"Free cancellation until {free_cancel_hours} hours before check-in")
        kept = min(b.unit_cents * b.quantity, b.total_cents)
        return Refund(b.total_cents - kept, f"Less than {free_cancel_hours} hours before check-in: "
                                            f"the first night ({fmt(kept)}) is kept")
    if b.kind == "flight":
        if now <= b.confirmed_at + timedelta(hours=24):
            return Refund(b.total_cents, "Free cancellation within 24 hours of booking")
        return Refund(0, "This fare is non-refundable after 24 hours")
    if b.kind == "tour":
        if now <= b.starts_at - timedelta(hours=24):
            return Refund(b.total_cents, "Free cancellation until 24 hours before the tour")
        return Refund(0, "Less than 24 hours before the tour: non-refundable")
    return Refund(b.total_cents, "Free cancellation before pick-up")


def cancel(db, user, booking_id, now=None):
    now = now or datetime.now()
    with _tx(db):
        b = _own_booking(db, user, booking_id, lock=True)
        refund = refund_for(b, now, _stay_cancel_hours(db, b))
        was_confirmed = b.status == "confirmed"
        b.status, b.cancelled_at = "cancelled", now
        b.refund_cents = refund.cents if was_confirmed else 0
        db.execute(ItineraryItem.__table__.delete().where(ItineraryItem.booking_id == b.id))
        _renumber_trip(db, b.trip_id)
    return b, refund


def refund_quote(db, user, booking_id, now=None):
    """What cancelling right now would refund, without cancelling."""
    b = _own_booking(db, user, booking_id)
    return refund_for(b, now or datetime.now(), _stay_cancel_hours(db, b))


def expire_holds(db, now=None):
    """Mark lapsed holds as expired (their stock is already free; this tidies the records)."""
    now = now or datetime.now()
    with _tx(db):
        lapsed = db.scalars(select(Booking).where(Booking.status == "held", Booking.held_until <= now)).all()
        for b in lapsed:
            b.status = "expired"
    return len(lapsed)


def bookings_for_trip(db, user, trip_id):
    trip = _own_trip(db, user, trip_id)
    return db.scalars(select(Booking).where(Booking.trip_id == trip.id).order_by(Booking.starts_at)).all()


# ======================================================================
# Availability arithmetic
# ======================================================================

def _holding_stock(now):
    """Bookings that still take up stock: confirmed ones, and holds that haven't run out."""
    return or_(Booking.status == "confirmed", (Booking.status == "held") & (Booking.held_until > now))


def _rooms_left(db, room, check_in, check_out, now):
    """The fewest rooms free on any single night of the stay."""
    overlapping = db.execute(select(Booking.start_date, Booking.end_date, Booking.quantity).where(
        Booking.room_type_id == room.id, _holding_stock(now),
        Booking.start_date < check_out, Booking.end_date > check_in)).all()
    worst = 0
    night = check_in
    while night < check_out:
        taken = sum(q for start, end, q in overlapping if start <= night < end)
        worst = max(worst, taken)
        night += timedelta(days=1)
    return room.rooms_total - worst


def _cars_left(db, car, pickup, dropoff, now):
    overlapping = db.execute(select(Booking.start_date, Booking.end_date, Booking.quantity).where(
        Booking.car_class_id == car.id, _holding_stock(now),
        Booking.start_date <= dropoff, Booking.end_date >= pickup)).all()
    worst = 0
    day = pickup
    while day <= dropoff:                          # a car is out from pick-up day to drop-off day inclusive
        worst = max(worst, sum(q for start, end, q in overlapping if start <= day <= end))
        day += timedelta(days=1)
    return car.cars_total - worst


def _seats_left(db, flight, now):
    taken = db.scalar(select(func.coalesce(func.sum(Booking.quantity), 0)).where(
        Booking.flight_id == flight.id, _holding_stock(now)))
    return flight.seats_total - taken


def _places_left(db, slot, now):
    taken = db.scalar(select(func.coalesce(func.sum(Booking.quantity), 0)).where(
        Booking.tour_slot_id == slot.id, _holding_stock(now)))
    return slot.capacity - taken


# ======================================================================
# Helpers
# ======================================================================

def _new_booking(db, trip, user, kind, now, **fields):
    b = Booking(reference=_reference(), trip_id=trip.id, user_id=user.id, kind=kind, status="held",
                total_cents=fields["unit_cents"] * fields["quantity"] * fields["units"], created_at=now,
                held_until=now + timedelta(minutes=HOLD_MINUTES), **fields)
    db.add(b)
    db.flush()
    return b


def _reference():
    """'JNT-7KQ2XM': short to read out on the phone, random so it can't be guessed. No 0/O or 1/I."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "JNT-" + "".join(secrets.choice(alphabet) for _ in range(6))


def _add_booking_to_plan(db, b):
    """A confirmed booking appears in the itinerary automatically."""
    entries = {
        "stay": [(b.start_date, f"Check in: {b.title}", CHECK_IN), (b.end_date, f"Check out: {b.title}", CHECK_OUT)],
        "flight": [(b.start_date, b.title, b.starts_at.time())],
        "tour": [(b.start_date, b.title, b.starts_at.time())],
        "car": [(b.start_date, f"Pick up car: {b.title}", CAR_PICKUP),
                (b.end_date, f"Drop off car: {b.title}", CAR_DROPOFF)],
    }[b.kind]
    for day, title, at in entries:
        if b.trip.start_date <= day <= b.trip.end_date:
            _slot_in(db, ItineraryItem(trip_id=b.trip_id, day=day, kind="booking", title=title[:120],
                                       time=at.strftime("%H:%M"), notes=f"Booking {b.reference}",
                                       booking_id=b.id))


def _stay_cancel_hours(db, b):
    if b.kind != "stay":
        return 48
    return db.scalar(select(Stay.free_cancel_hours).join(RoomType).where(RoomType.id == b.room_type_id))


def _check_trip_fields(title, start, end, travellers, budget_cents, now, allow_past=False):
    _title(title)
    if not allow_past and start < now.date():
        raise JauntError("The trip can't start in the past")
    if end < start:
        raise JauntError("The trip must end on or after the day it starts")
    if (end - start).days + 1 > MAX_TRIP_DAYS:
        raise JauntError(f"Trips can be up to {MAX_TRIP_DAYS} days long")
    if not isinstance(travellers, int) or not 1 <= travellers <= 9:
        raise JauntError("Travellers must be between 1 and 9")
    if not isinstance(budget_cents, int) or budget_cents < 0:
        raise JauntError("The budget can't be negative")


def _check_day(trip, day):
    if not trip.start_date <= day <= trip.end_date:
        raise JauntError(f"That day isn't part of this trip ({trip.start_date:%d %b} to {trip.end_date:%d %b})")


def _within_trip(trip, first, last):
    if first < trip.start_date or last > trip.end_date:
        raise JauntError(f"That's outside your trip dates ({trip.start_date:%d %b} to {trip.end_date:%d %b})")


def _nights(check_in, check_out):
    nights = (check_out - check_in).days
    if nights < 1:
        raise JauntError("Check-out must be at least a day after check-in")
    if nights > MAX_TRIP_DAYS:
        raise JauntError(f"Stays can be up to {MAX_TRIP_DAYS} nights")
    return nights


def _car_days(pickup, dropoff):
    if dropoff < pickup:
        raise JauntError("Drop-off can't be before pick-up")
    return (dropoff - pickup).days + 1


def _quantity(n, most, what, why=""):
    if not isinstance(n, int) or not 1 <= n <= most:
        raise JauntError(f"You can book between 1 and {most} {what} {why}".strip())


def _sold_out(left, unit, name):
    if left <= 0:
        return f"Sorry, {name} is fully booked for those dates"
    return f"Only {left} {unit}{'s' if left != 1 else ''} left on {name}"


def _title(title):
    title = (title or "").strip()
    if not title:
        raise JauntError("Please give it a name")
    if len(title) > 120:
        raise JauntError("Please keep the name under 120 characters")
    return title


def _time_text(text):
    """'9:30' -> '09:30'. Blank means 'any time'."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise JauntError("Times look like 09:30") from None


def _slot_in(db, item):
    """Add a new item to its day. A timed item goes before the first item that starts later,
    so the day reads in time order; an item with no time goes at the end. After that the
    traveller's own drag-and-drop order is left alone."""
    items = _day_items(db, item.trip_id, item.day)
    at = len(items)
    if item.time:
        at = next((n for n, i in enumerate(items) if i.time and i.time > item.time), at)
    items.insert(at, item)
    for pos, i in enumerate(items):
        i.position = pos
    db.add(item)
    db.flush()


def _day_items(db, trip_id, day):
    return list(db.scalars(select(ItineraryItem).where(ItineraryItem.trip_id == trip_id, ItineraryItem.day == day)
                           .order_by(ItineraryItem.position, ItineraryItem.id)))


def _renumber_trip(db, trip_id):
    db.flush()
    days = db.scalars(select(ItineraryItem.day).where(ItineraryItem.trip_id == trip_id).distinct()).all()
    for day in days:
        for pos, item in enumerate(_day_items(db, trip_id, day)):
            item.position = pos


def _get(db, model, obj_id, lock=False):
    q = select(model).where(model.id == obj_id)
    if lock:
        q = q.with_for_update(of=model)
    obj = db.scalar(q)
    if obj is None:
        raise NotFound(f"{model.__name__} not found")
    return obj


def _own_trip(db, user, trip_id, lock=False):
    trip = _get(db, Trip, trip_id, lock=lock)
    if trip.owner_id != user.id:
        raise NotFound("Trip not found")          # someone else's trip looks exactly like a missing one
    return trip


def _own_item(db, user, item_id, lock_trip=False):
    item = _get(db, ItineraryItem, item_id)
    return item, _own_trip(db, user, item.trip_id, lock=lock_trip)


def _own_booking(db, user, booking_id, lock=False):
    b = _get(db, Booking, booking_id, lock=lock)
    if b.user_id != user.id:
        raise NotFound("Booking not found")
    return b
