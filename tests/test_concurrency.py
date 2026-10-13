"""Five travellers try to grab the only two-bed villa at the same instant."""

import threading

from jaunt import services
from jaunt.services import JauntError

from .conftest import NOW, PASSWORD, d, room


def test_the_last_room_goes_to_exactly_one_traveller(Session, db, places):
    villa = room(db, "Seagrape Villas", "Two-bed Villa")             # 1 room
    people = []
    for i in range(5):
        u = services.create_user(db, f"t{i}@example.test", f"T{i}", PASSWORD, now=NOW)
        t = services.create_trip(db, u, "Bequia", places["BQU"], d(10), d(14), now=NOW)
        people.append((u, t))
    db.rollback()                                                    # release any read lock before the race
    start = threading.Barrier(len(people))
    outcomes = []

    def grab(user, trip):
        session = Session()
        start.wait()
        try:
            services.hold_stay(session, user, trip.id, villa.id, d(10), d(14), now=NOW)
            outcomes.append("held")
        except JauntError:
            outcomes.append("sold out")
        finally:
            session.close()

    threads = [threading.Thread(target=grab, args=p) for p in people]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["held"] + ["sold out"] * 4


def test_two_overlapping_stays_confirmed_at_once_only_one_wins(Session, db, places, jo, trip, monkeypatch):
    """Two browser tabs press Confirm on different stays for the same nights at the same instant.

    To force the worst timing every run, each confirmation pauses right after its "is there a clash?"
    check, waiting (up to a second) for the other to reach the same point. Without the trip lock both
    checks pass and both stays get confirmed. With it, the second waits for the first to finish.
    """
    holds = [services.hold_stay(db, jo, trip.id, room(db, stay, name).id, d(10), d(14), now=NOW).id
             for stay, name in (("Harbour Lights Inn", "Harbour Double"), ("Seagrape Villas", "One-bed Villa"))]
    db.rollback()
    check = services._one_stay_a_night
    both_checked = threading.Barrier(2, timeout=1)

    def check_then_pause(*args):
        check(*args)
        try:
            both_checked.wait()
        except threading.BrokenBarrierError:
            pass                                   # the other one is (correctly) still waiting for the lock

    monkeypatch.setattr(services, "_one_stay_a_night", check_then_pause)
    start = threading.Barrier(2)
    outcomes = []

    def press_confirm(booking_id):
        session = Session()
        start.wait()
        try:
            services.confirm(session, jo, booking_id, now=NOW)
            outcomes.append("confirmed")
        except JauntError as e:
            outcomes.append("refused" if "already have" in str(e) else str(e))
        finally:
            session.close()

    threads = [threading.Thread(target=press_confirm, args=(b,)) for b in holds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["confirmed", "refused"]
