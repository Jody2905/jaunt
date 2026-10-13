import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from jaunt import services
from jaunt.models import Booking
from jaunt.services import JauntError, NotFound

from .conftest import NOW, car, d, flight, room, slot


# ---- Searching ---------------------------------------------------------------------------

def test_search_stays_shows_price_and_rooms_left(db, places):
    options = services.search_stays(db, places["BQU"], d(10), d(13), rooms=1, now=NOW)
    names = [(o.room.stay.name, o.room.name, o.rooms_left, o.total_cents) for o in options]
    assert ("Harbour Lights Inn", "Harbour Double", 5, 3 * 11000) in names
    assert ("Seagrape Villas", "Two-bed Villa", 1, 3 * 38000) in names
    nightly = [o.room.nightly_cents for o in options]
    assert nightly == sorted(nightly)                               # cheapest first


def test_search_flights_and_tours_and_cars(db, places):
    flights = services.search_flights(db, places["SVD"], places["BQU"], d(10), passengers=2, now=NOW)
    assert [f.flight.number for f in flights] == ["IH 11", "IH 31"]
    assert flights[0].total_cents == 2 * 7900
    tours = services.search_tours(db, places["BQU"], d(11), people=2, now=NOW)
    assert [t.slot.tour.name for t in tours] == ["Sunset Sail Around Bequia"]
    cars = services.search_cars(db, places["BQU"], d(11), d(13), now=NOW)
    assert cars[0].days == 3 and cars[0].total_cents == 3 * 7000


def test_no_sunday_catamaran(db, places):
    assert d(15).weekday() == 6
    assert "Tobago Cays Snorkel Day" in [t.slot.tour.name for t in services.search_tours(db, places["SVD"], d(14), now=NOW)]
    names = [t.slot.tour.name for t in services.search_tours(db, places["SVD"], d(15), now=NOW)]
    assert "Tobago Cays Snorkel Day" not in names                   # 15 Nov 2026 is a Sunday


# ---- Holding stock ---------------------------------------------------------------------------

def test_a_hold_sets_stock_aside_for_15_minutes(db, jo, trip, places):
    villa = room(db, "Seagrape Villas", "Two-bed Villa")          # only 1 of these
    b = services.hold_stay(db, jo, trip.id, villa.id, d(10), d(13), now=NOW)
    assert b.status == "held" and b.held_until == NOW + timedelta(minutes=15)
    assert re.fullmatch(r"JNT-[A-HJ-NP-Z2-9]{6}", b.reference)
    assert b.total_cents == 3 * 38000
    assert villa.id not in [o.room.id for o in services.search_stays(db, places["BQU"], d(11), d(12), now=NOW)]
    later = NOW + timedelta(minutes=16)                              # the hold lapses: the villa is free again
    assert villa.id in [o.room.id for o in services.search_stays(db, places["BQU"], d(11), d(12), now=later)]


def test_rooms_are_counted_night_by_night(db, jo, friend, places, trip):
    """Two back-to-back stays share a room type without clashing; an overlapping one doesn't."""
    villa = room(db, "Seagrape Villas", "Two-bed Villa")
    services.hold_stay(db, jo, trip.id, villa.id, d(10), d(12), now=NOW)
    other = services.create_trip(db, friend, "Kemar's week", places["BQU"], d(10), d(16), now=NOW)
    services.hold_stay(db, friend, other.id, villa.id, d(12), d(14), now=NOW)       # check-in on check-out day: fine
    with pytest.raises(JauntError, match="fully booked"):
        services.hold_stay(db, friend, other.id, villa.id, d(13), d(15), now=NOW)


def test_bookings_must_fit_the_trip(db, jo, trip, places):
    with pytest.raises(JauntError, match="outside your trip dates"):
        services.hold_stay(db, jo, trip.id, room(db, "Harbour Lights Inn", "Harbour Double").id, d(15), d(18), now=NOW)
    with pytest.raises(JauntError, match="between 1 and 2 passengers"):
        services.hold_flight(db, jo, trip.id, flight(db, "IH 11", d(10)).id, passengers=3, now=NOW)
    with pytest.raises(JauntError, match="at least a day"):
        services.hold_stay(db, jo, trip.id, room(db, "Harbour Lights Inn", "Harbour Double").id, d(12), d(12), now=NOW)


def test_small_plane_fills_up(db, jo, places):
    big = services.create_trip(db, jo, "Family trip", places["BQU"], d(10), d(12), travellers=9, now=NOW)
    island_hopper = flight(db, "IH 11", d(10))                       # 9 seats
    services.hold_flight(db, jo, big.id, island_hopper.id, passengers=7, now=NOW)
    with pytest.raises(JauntError, match="Only 2 seats left on flight IH 11"):
        services.hold_flight(db, jo, big.id, island_hopper.id, passengers=3, now=NOW)


# ---- Confirming --------------------------------------------------------------------------------

def test_confirming_puts_the_booking_in_the_plan(db, jo, trip):
    b = services.hold_stay(db, jo, trip.id, room(db, "Harbour Lights Inn", "Harbour Double").id, d(10), d(14), now=NOW)
    services.confirm(db, jo, b.id, now=NOW + timedelta(minutes=5))
    days = services.itinerary(db, jo, trip.id)
    assert [(i.title, i.time) for i in days[d(10)]] == [("Check in: Harbour Lights Inn, Harbour Double", "14:00")]
    assert [(i.title, i.time) for i in days[d(14)]] == [("Check out: Harbour Lights Inn, Harbour Double", "11:00")]


def test_a_lapsed_hold_cannot_be_confirmed(db, jo, trip):
    b = services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, people=2, now=NOW)
    with pytest.raises(JauntError, match="hold has expired"):
        services.confirm(db, jo, b.id, now=NOW + timedelta(minutes=15))
    assert db.get(Booking, b.id).status == "expired"


def test_confirm_twice_or_someone_elses(db, jo, friend, trip):
    b = services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, now=NOW)
    with pytest.raises(NotFound):
        services.confirm(db, friend, b.id, now=NOW)
    services.confirm(db, jo, b.id, now=NOW)
    with pytest.raises(JauntError, match="is confirmed"):
        services.confirm(db, jo, b.id, now=NOW)


def test_expire_holds_tidies_records(db, jo, trip):
    services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, now=NOW)
    assert services.expire_holds(db, now=NOW + timedelta(minutes=10)) == 0
    assert services.expire_holds(db, now=NOW + timedelta(minutes=20)) == 1


# ---- Cancelling and refunds ----------------------------------------------------------------------

def booked(db, jo, hold):
    return services.confirm(db, jo, hold.id, now=NOW)


def test_stay_free_cancellation_then_first_night_kept(db, jo, trip):
    villa = room(db, "Seagrape Villas", "One-bed Villa")           # free until 72 hours before check-in
    b = booked(db, jo, services.hold_stay(db, jo, trip.id, villa.id, d(10), d(13), now=NOW))
    check_in = datetime(2026, 11, 10, 14, 0)
    assert services.refund_for(b, check_in - timedelta(hours=73), 72).cents == b.total_cents
    late = services.refund_for(b, check_in - timedelta(hours=10), 72)
    assert late.cents == b.total_cents - 24000 and "first night" in late.reason
    cancelled, refund = services.cancel(db, jo, b.id, now=check_in - timedelta(hours=10))
    assert cancelled.status == "cancelled" and cancelled.refund_cents == 2 * 24000
    assert services.itinerary(db, jo, trip.id)[d(10)] == []          # gone from the plan


def test_flight_refund_window(db, jo, trip):
    b = booked(db, jo, services.hold_flight(db, jo, trip.id, flight(db, "IH 11", d(10)).id, 2, now=NOW))
    assert services.refund_for(b, NOW + timedelta(hours=23), 48).cents == 2 * 7900
    assert services.refund_for(b, NOW + timedelta(hours=25), 48).cents == 0


def test_tour_and_car_policies(db, jo, trip):
    sail = booked(db, jo, services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, 2, now=NOW))
    starts = datetime(2026, 11, 11, 16, 30)
    assert services.refund_for(sail, starts - timedelta(hours=25), 48).cents == sail.total_cents
    assert services.refund_for(sail, starts - timedelta(hours=2), 48).cents == 0
    moke = booked(db, jo, services.hold_car(db, jo, trip.id, car(db, "Bequia Wheels", "Mini Moke").id, d(11), d(13), now=NOW))
    assert services.refund_for(moke, datetime(2026, 11, 11, 8, 59), 48).cents == moke.total_cents
    with pytest.raises(JauntError, match="already started"):
        services.cancel(db, jo, moke.id, now=datetime(2026, 11, 11, 9, 0))


def test_cancelling_a_hold_costs_nothing_and_frees_stock(db, jo, trip, places):
    villa = room(db, "Seagrape Villas", "Two-bed Villa")
    b = services.hold_stay(db, jo, trip.id, villa.id, d(10), d(12), now=NOW)
    cancelled, refund = services.cancel(db, jo, b.id, now=NOW)
    assert refund.cents == 0 and cancelled.refund_cents == 0
    assert villa.id in [o.room.id for o in services.search_stays(db, places["BQU"], d(10), d(12), now=NOW)]


# ---- Budget -------------------------------------------------------------------------------------

def test_budget_counts_confirmed_held_and_kept_amounts(db, jo, trip):
    stay = booked(db, jo, services.hold_stay(db, jo, trip.id, room(db, "Seagrape Villas", "One-bed Villa").id,
                                             d(10), d(16), now=NOW))       # 6 nights x 240 = 1,440
    services.hold_flight(db, jo, trip.id, flight(db, "IH 11", d(10)).id, 2, now=NOW)        # 158 on hold
    b = services.budget(db, jo, trip.id, now=NOW)
    assert (b.committed_cents, b.on_hold_cents, b.remaining_cents) == (144000, 15800, 300000 - 144000 - 15800)
    services.cancel(db, jo, stay.id, now=datetime(2026, 11, 9, 12, 0))   # late: first night kept
    assert services.budget(db, jo, trip.id, now=NOW).committed_cents == 24000
    assert not b.over_budget


def test_travellers_cannot_drop_below_booked_people(db, jo, trip):
    services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, people=2, now=NOW)
    with pytest.raises(JauntError, match="bookings for 2 people"):
        services.update_trip(db, jo, trip.id, travellers=1, now=NOW)


def test_booking_items_stay_on_their_day_and_cannot_be_deleted(db, jo, trip):
    booked(db, jo, services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, now=NOW))
    item = services.itinerary(db, jo, trip.id)[d(11)][0]
    with pytest.raises(JauntError, match="Cancel the booking"):
        services.delete_item(db, jo, item.id)
    with pytest.raises(JauntError, match="stays on its own day"):
        services.move_item(db, jo, item.id, d(12), 0)
    services.update_item(db, jo, item.id, notes="Bring a jumper")
    with pytest.raises(JauntError, match="only edit their notes"):
        services.update_item(db, jo, item.id, title="Renamed")


def test_trip_with_live_bookings_cannot_be_deleted(db, jo, trip):
    b = services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, now=NOW)
    with pytest.raises(JauntError, match="Cancel this trip's bookings"):
        services.delete_trip(db, jo, trip.id, now=NOW)
    services.cancel(db, jo, b.id, now=NOW)
    services.delete_trip(db, jo, trip.id, now=NOW)
    assert services.trips_for(db, jo) == []


def test_database_checks_bookings(db, jo, trip):
    b = services.hold_tour(db, jo, trip.id, slot(db, "Sunset Sail Around Bequia", d(11)).id, people=2, now=NOW)
    with pytest.raises(IntegrityError):
        db.query(Booking).filter_by(id=b.id).update({"total_cents": 1})
        db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.query(Booking).filter_by(id=b.id).update({"flight_id": 1})     # a tour booking can't also be a flight
        db.commit()
    db.rollback()
