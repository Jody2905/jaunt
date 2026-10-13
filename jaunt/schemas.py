"""The shapes of the API's requests and responses.

Requests are strict: unknown fields are rejected and numbers must be real JSON
numbers (not "2" or true), so a typo can't silently do something unexpected.
The business rules themselves (dates inside the trip, travellers 1-9...) are
checked in services.py, in one place, for the API and everything else.
Money is always whole US cents.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Int = Annotated[int, Field(strict=True)]
Count = Annotated[int, Field(strict=True, ge=1, le=9)]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Accounts -----------------------------------------------------------------------------

class RegisterIn(In):
    email: str = Field(max_length=254, examples=["sam@example.test"])
    name: str = Field(max_length=80, examples=["Sam"])
    password: str = Field(max_length=200, examples=["a-long-passphrase"])


class LoginIn(In):
    email: str = Field(max_length=254, examples=["jo@example.test"])
    password: str = Field(max_length=200, examples=["Jaunt-2026-demo"])


class UserOut(Out):
    id: int
    email: str
    name: str


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str = Field(description="Send this back in the X-CSRF-Token header on every change")


# ---- Places and trips ------------------------------------------------------------------------

class PlaceOut(Out):
    id: int
    code: str
    name: str
    country: str
    blurb: str


class TripIn(In):
    title: str = Field(max_length=100, examples=["Bequia birthday week"])
    place_id: Int
    start_date: date
    end_date: date
    travellers: Int = 1
    budget_cents: Int = Field(0, examples=[250000])


class TripPatch(In):
    title: str | None = Field(None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    travellers: Int | None = None
    budget_cents: Int | None = None


class TripOut(Out):
    id: int
    title: str
    place: PlaceOut
    start_date: date
    end_date: date
    days: int
    travellers: int
    budget_cents: int


class ItemIn(In):
    day: date
    title: str = Field(max_length=120, examples=["Princess Margaret Beach"])
    kind: Literal["activity", "food", "transport", "note"] = "activity"
    time: str | None = Field(None, max_length=5, examples=["10:00"])
    notes: str = Field("", max_length=2000)


class ItemPatch(In):
    title: str | None = Field(None, max_length=120)
    kind: Literal["activity", "food", "transport", "note"] | None = None
    time: str | None = Field(None, max_length=5)
    notes: str | None = Field(None, max_length=2000)


class MoveIn(In):
    day: date
    position: Annotated[int, Field(strict=True, ge=0)]


class ItemOut(Out):
    id: int
    day: date
    position: int
    kind: str
    title: str
    time: str | None
    notes: str
    booking_id: int | None


class DayOut(BaseModel):
    date: date
    items: list[ItemOut]


class BudgetOut(BaseModel):
    budget_cents: int
    committed_cents: int
    on_hold_cents: int
    remaining_cents: int
    over_budget: bool


class BookingOut(Out):
    id: int
    reference: str
    kind: str
    status: Literal["held", "confirmed", "cancelled", "expired"]
    title: str
    start_date: date
    end_date: date
    starts_at: datetime
    quantity: int
    units: int
    unit_cents: int
    total_cents: int
    refund_cents: int
    held_until: datetime
    hold_seconds_left: int | None = Field(None, description="For holds: seconds until it lapses")
    confirmed_at: datetime | None
    cancelled_at: datetime | None


class TripDetailOut(TripOut):
    itinerary: list[DayOut]
    budget: BudgetOut
    bookings: list[BookingOut]


# ---- Search ------------------------------------------------------------------------------------

class StayOptionOut(BaseModel):
    room_type_id: int
    stay: str
    stay_kind: str
    stars: int
    room: str
    sleeps: int
    blurb: str
    free_cancel_hours: int
    nightly_cents: int
    nights: int
    rooms_left: int
    total_cents: int


class FlightOptionOut(BaseModel):
    flight_id: int
    number: str
    airline: str
    origin: str
    destination: str
    departs_at: datetime
    arrives_at: datetime
    fare_cents: int
    seats_left: int
    total_cents: int


class TourOptionOut(BaseModel):
    slot_id: int
    tour: str
    operator: str
    blurb: str
    starts_at: datetime
    minutes: int
    price_cents: int
    places_left: int
    total_cents: int


class CarOptionOut(BaseModel):
    car_class_id: int
    company: str
    name: str
    seats: int
    daily_cents: int
    days: int
    cars_left: int
    total_cents: int


# ---- Holds and bookings -------------------------------------------------------------------------

class StayHoldIn(In):
    room_type_id: Int
    check_in: date
    check_out: date
    rooms: Annotated[int, Field(strict=True, ge=1, le=5)] = 1


class FlightHoldIn(In):
    flight_id: Int
    passengers: Count = 1


class TourHoldIn(In):
    slot_id: Int
    people: Count = 1


class CarHoldIn(In):
    car_class_id: Int
    pickup: date
    dropoff: date
    cars: Annotated[int, Field(strict=True, ge=1, le=3)] = 1


class RefundOut(BaseModel):
    refund_cents: int
    reason: str


class CancelOut(BaseModel):
    booking: BookingOut
    refund_cents: int
    reason: str


class Problem(BaseModel):
    detail: str = Field(description="A message that's safe to show the traveller")
