"""Jaunt's tables.

Two halves:
  * the traveller's side: trips, and the day-by-day itinerary inside each trip
  * the travel inventory: places, stays (with room types), flights, tours (with
    time slots) and car hire, which travellers search and book

Availability is never stored as a counter that could drift. It is always
worked out from the bookings that are still holding stock (held and not yet
expired, or confirmed), so "rooms left" can't disagree with the bookings.

The database enforces the important rules too (CHECK, UNIQUE, foreign keys).
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

BOOKING_KINDS = ("stay", "flight", "tour", "car")
BOOKING_STATUSES = ("held", "confirmed", "cancelled", "expired")
ITEM_KINDS = ("activity", "food", "transport", "note", "booking")


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


# ======================================================================
# People and trips
# ======================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]


class LoginSession(Base):
    """A signed-in browser. Only a hash of the cookie token is kept (see services.start_session)."""
    __tablename__ = "login_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class Place(Base):
    __tablename__ = "places"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(3), unique=True)       # airport code, e.g. SVD
    name: Mapped[str] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(80))
    blurb: Mapped[str] = mapped_column(Text, default="")


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="trip_ends_after_start"),
        CheckConstraint("travellers BETWEEN 1 AND 9", name="trip_travellers_1_to_9"),
        CheckConstraint("budget_cents >= 0", name="trip_budget_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(100))
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    start_date: Mapped[date]
    end_date: Mapped[date]
    travellers: Mapped[int]
    budget_cents: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]

    place: Mapped[Place] = relationship(lazy="joined")

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class ItineraryItem(Base):
    """One thing on one day of a trip. Items keep an explicit order within their day,
    so the traveller can drag them around."""
    __tablename__ = "itinerary_items"
    __table_args__ = (
        CheckConstraint(_in("kind", ITEM_KINDS), name="item_kind_known"),
        CheckConstraint("position >= 0", name="item_position_not_negative"),
        CheckConstraint("(kind = 'booking') = (booking_id IS NOT NULL)", name="booking_items_point_at_a_booking"),
        Index("ix_items_trip_day", "trip_id", "day", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"))
    day: Mapped[date]
    position: Mapped[int]
    kind: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(120))
    time: Mapped[str | None] = mapped_column(String(5))           # "09:30", optional
    notes: Mapped[str] = mapped_column(Text, default="")
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"))


# ======================================================================
# The travel inventory
# ======================================================================

class Stay(Base):
    __tablename__ = "stays"

    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))                 # hotel, guesthouse, villa
    stars: Mapped[int]
    blurb: Mapped[str] = mapped_column(Text, default="")
    free_cancel_hours: Mapped[int] = mapped_column(default=48)     # before check-in

    place: Mapped[Place] = relationship(lazy="joined")
    rooms: Mapped[list["RoomType"]] = relationship(back_populates="stay", lazy="selectin")


class RoomType(Base):
    __tablename__ = "room_types"
    __table_args__ = (CheckConstraint("rooms_total > 0 AND sleeps > 0 AND nightly_cents > 0",
                                      name="room_type_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id"))
    name: Mapped[str] = mapped_column(String(80))
    sleeps: Mapped[int]
    nightly_cents: Mapped[int]
    rooms_total: Mapped[int]

    stay: Mapped[Stay] = relationship(back_populates="rooms", lazy="joined")


class Flight(Base):
    __tablename__ = "flights"
    __table_args__ = (
        CheckConstraint("arrives_at > departs_at", name="flight_arrives_after_departing"),
        CheckConstraint("origin_id <> destination_id", name="flight_goes_somewhere"),
        CheckConstraint("seats_total > 0 AND fare_cents > 0", name="flight_positive"),
        UniqueConstraint("number", "departs_at", name="one_flight_number_per_departure"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(8))                # e.g. "BW 214"
    airline: Mapped[str] = mapped_column(String(60))
    origin_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    destination_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    departs_at: Mapped[datetime]                                  # local time at the origin
    arrives_at: Mapped[datetime]
    fare_cents: Mapped[int]                                       # per passenger
    seats_total: Mapped[int]

    origin: Mapped[Place] = relationship(foreign_keys=[origin_id], lazy="joined")
    destination: Mapped[Place] = relationship(foreign_keys=[destination_id], lazy="joined")


class Tour(Base):
    __tablename__ = "tours"
    __table_args__ = (CheckConstraint("price_cents > 0 AND minutes > 0", name="tour_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    name: Mapped[str] = mapped_column(String(100))
    operator: Mapped[str] = mapped_column(String(80))
    blurb: Mapped[str] = mapped_column(Text, default="")
    minutes: Mapped[int]
    price_cents: Mapped[int]                                      # per person

    place: Mapped[Place] = relationship(lazy="joined")


class TourSlot(Base):
    __tablename__ = "tour_slots"
    __table_args__ = (CheckConstraint("capacity > 0", name="slot_capacity_positive"),
                      UniqueConstraint("tour_id", "starts_at", name="one_slot_per_start"))

    id: Mapped[int] = mapped_column(primary_key=True)
    tour_id: Mapped[int] = mapped_column(ForeignKey("tours.id"))
    starts_at: Mapped[datetime]
    capacity: Mapped[int]

    tour: Mapped[Tour] = relationship(lazy="joined")


class CarClass(Base):
    __tablename__ = "car_classes"
    __table_args__ = (CheckConstraint("cars_total > 0 AND daily_cents > 0 AND seats > 0", name="car_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    company: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(60))                 # e.g. "Compact 4x4"
    seats: Mapped[int]
    daily_cents: Mapped[int]
    cars_total: Mapped[int]

    place: Mapped[Place] = relationship(lazy="joined")


# ======================================================================
# Bookings
# ======================================================================

class Booking(Base):
    """One booking of one thing. A booking starts as a 15-minute HOLD (stock set aside
    while the traveller decides), then is CONFIRMED, or lapses (EXPIRED), or is CANCELLED.

    Exactly one of room_type_id / flight_id / tour_slot_id / car_class_id is set,
    matching `kind`; the database checks that.
    """
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(_in("kind", BOOKING_KINDS), name="booking_kind_known"),
        CheckConstraint(_in("status", BOOKING_STATUSES), name="booking_status_known"),
        CheckConstraint("quantity > 0", name="booking_quantity_positive"),
        CheckConstraint("total_cents = unit_cents * quantity * units", name="booking_total_adds_up"),
        CheckConstraint("refund_cents >= 0 AND refund_cents <= total_cents", name="refund_within_total"),
        CheckConstraint("end_date >= start_date", name="booking_ends_after_start"),
        CheckConstraint(
            "(kind = 'stay') = (room_type_id IS NOT NULL) AND (kind = 'flight') = (flight_id IS NOT NULL) "
            "AND (kind = 'tour') = (tour_slot_id IS NOT NULL) AND (kind = 'car') = (car_class_id IS NOT NULL)",
            name="booking_points_at_one_thing"),
        Index("ix_bookings_room_dates", "room_type_id", "status", "start_date", "end_date"),
        Index("ix_bookings_car_dates", "car_class_id", "status", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(12), unique=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(10))

    room_type_id: Mapped[int | None] = mapped_column(ForeignKey("room_types.id"))
    flight_id: Mapped[int | None] = mapped_column(ForeignKey("flights.id"))
    tour_slot_id: Mapped[int | None] = mapped_column(ForeignKey("tour_slots.id"))
    car_class_id: Mapped[int | None] = mapped_column(ForeignKey("car_classes.id"))

    title: Mapped[str] = mapped_column(String(160))               # what it is, frozen at booking time
    start_date: Mapped[date]                                      # check-in / flight day / tour day / pick-up
    end_date: Mapped[date]                                        # check-out / same day / same day / drop-off
    starts_at: Mapped[datetime]                                   # exact start, for cancellation rules
    quantity: Mapped[int]                                         # rooms, passengers, people or cars
    units: Mapped[int]                                            # nights or days (1 for flights and tours)
    unit_cents: Mapped[int]                                       # price per room-night, seat, person or car-day
    total_cents: Mapped[int]
    refund_cents: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime]
    held_until: Mapped[datetime]
    confirmed_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]

    trip: Mapped[Trip] = relationship(lazy="joined")
