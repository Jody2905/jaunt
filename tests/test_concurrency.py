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
