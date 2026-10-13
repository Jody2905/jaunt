import pytest
from sqlalchemy.exc import IntegrityError

from jaunt import services
from jaunt.models import ItineraryItem, Trip
from jaunt.services import JauntError, NotFound

from .conftest import NOW, PASSWORD, d


# ---- Accounts ----------------------------------------------------------------------------

def test_accounts(db):
    services.create_user(db, "Jo@Example.test", "Jo", PASSWORD, now=NOW)
    with pytest.raises(JauntError, match="already an account"):
        services.create_user(db, "jo@example.TEST", "Jo again", PASSWORD, now=NOW)
    assert services.authenticate(db, "JO@example.test", PASSWORD).name == "Jo"
    assert services.authenticate(db, "jo@example.test", "wrong-password") is None
    assert services.authenticate(db, "nobody@example.test", PASSWORD) is None
    with pytest.raises(JauntError, match="at least 10"):
        services.create_user(db, "x@example.test", "X", "short", now=NOW)


# ---- Trips ---------------------------------------------------------------------------------

def test_new_trip_has_one_empty_day_per_date(db, jo, trip):
    days = services.itinerary(db, jo, trip.id)
    assert list(days) == [d(n) for n in range(10, 17)]
    assert all(items == [] for items in days.values())
    assert trip.days == 7


@pytest.mark.parametrize("start, end, travellers, budget, message", [
    (d(1), d(5), 2, 0, "can't start in the past"),
    (d(10), d(9), 2, 0, "end on or after"),
    (d(10), d(10, 12), 2, 0, "up to 30 days"),            # 31 days; 30 is the limit
    (d(10), d(12), 0, 0, "between 1 and 9"),
    (d(10), d(12), 10, 0, "between 1 and 9"),
    (d(10), d(12), 2, -1, "can't be negative"),
])
def test_trip_rules(db, jo, places, start, end, travellers, budget, message):
    with pytest.raises(JauntError, match=message):
        services.create_trip(db, jo, "Bad trip", places["SVD"], start, end, travellers, budget, now=NOW)


def test_someone_elses_trip_looks_missing(db, friend, trip):
    for action in (lambda: services.get_trip(db, friend, trip.id),
                   lambda: services.itinerary(db, friend, trip.id),
                   lambda: services.add_item(db, friend, trip.id, d(10), "Sneaky"),
                   lambda: services.update_trip(db, friend, trip.id, title="Mine now", now=NOW),
                   lambda: services.delete_trip(db, friend, trip.id, now=NOW)):
        with pytest.raises(NotFound, match="Trip not found"):
            action()


def test_trips_are_listed_in_date_order(db, jo, places, trip):
    services.create_trip(db, jo, "Weekend in Grenada", places["GND"], d(5), d(7), now=NOW)
    assert [t.title for t in services.trips_for(db, jo)] == ["Weekend in Grenada", "Bequia birthday week"]


# ---- The itinerary ----------------------------------------------------------------------------

def add(db, jo, trip, day, *titles):
    return [services.add_item(db, jo, trip.id, day, t) for t in titles]


def titles(db, jo, trip, day):
    return [i.title for i in services.itinerary(db, jo, trip.id)[day]]


def test_items_keep_their_order_and_times_are_tidied(db, jo, trip):
    beach, lunch = add(db, jo, trip, d(11), "Princess Margaret Beach", "Lunch at Jack's")
    services.update_item(db, jo, lunch.id, time_text="9:30", kind="food")
    assert titles(db, jo, trip, d(11)) == ["Princess Margaret Beach", "Lunch at Jack's"]
    assert services.itinerary(db, jo, trip.id)[d(11)][1].time == "09:30"
    with pytest.raises(JauntError, match="09:30"):
        services.update_item(db, jo, lunch.id, time_text="half nine")


def test_drag_within_a_day_and_between_days(db, jo, trip):
    a, b, c = add(db, jo, trip, d(11), "A", "B", "C")
    services.move_item(db, jo, c.id, d(11), 0)
    assert titles(db, jo, trip, d(11)) == ["C", "A", "B"]
    services.move_item(db, jo, a.id, d(12), 0)
    assert titles(db, jo, trip, d(11)) == ["C", "B"]
    assert titles(db, jo, trip, d(12)) == ["A"]
    positions = sorted(i.position for i in services.itinerary(db, jo, trip.id)[d(11)])
    assert positions == [0, 1]                                      # no gaps after the move
    services.move_item(db, jo, b.id, d(12), 99)                     # past the end -> goes last
    assert titles(db, jo, trip, d(12)) == ["A", "B"]


def test_items_must_be_on_trip_days(db, jo, trip):
    with pytest.raises(JauntError, match="isn't part of this trip"):
        services.add_item(db, jo, trip.id, d(20), "Too late")
    item, = add(db, jo, trip, d(11), "Kayaking")
    with pytest.raises(JauntError, match="isn't part of this trip"):
        services.move_item(db, jo, item.id, d(9), 0)


def test_delete_renumbers(db, jo, trip):
    a, b, c = add(db, jo, trip, d(13), "A", "B", "C")
    services.delete_item(db, jo, b.id)
    assert [(i.title, i.position) for i in services.itinerary(db, jo, trip.id)[d(13)]] == [("A", 0), ("C", 1)]


def test_dates_cannot_shrink_past_planned_days(db, jo, trip):
    add(db, jo, trip, d(16), "Last swim")
    with pytest.raises(JauntError, match="1 plan item"):
        services.update_trip(db, jo, trip.id, end_date=d(14), now=NOW)
    t = services.update_trip(db, jo, trip.id, end_date=d(18), title="  Longer trip ", now=NOW)
    assert (t.end_date, t.title) == (d(18), "Longer trip")


def test_database_backs_up_the_rules(db, jo, trip):
    with pytest.raises(IntegrityError):
        db.query(Trip).filter_by(id=trip.id).update({"travellers": 12})
        db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.add(ItineraryItem(trip_id=trip.id, day=d(11), position=0, kind="booking", title="Fake booking"))
        db.commit()                                                 # a booking item must point at a booking
    db.rollback()


def test_timed_items_slot_into_time_order(db, jo, trip):
    services.add_item(db, jo, trip.id, d(12), "Lunch at the Fig Tree", kind="food", time_text="12:30")
    services.add_item(db, jo, trip.id, d(12), "Pack a picnic")                     # no time: goes last
    services.add_item(db, jo, trip.id, d(12), "Princess Margaret Beach", time_text="10:00")
    services.add_item(db, jo, trip.id, d(12), "Sundowners", time_text="18:00")
    assert titles(db, jo, trip, d(12)) == ["Princess Margaret Beach", "Lunch at the Fig Tree", "Pack a picnic",
                                           "Sundowners"]
    assert [i.position for i in services.itinerary(db, jo, trip.id)[d(12)]] == [0, 1, 2, 3]
