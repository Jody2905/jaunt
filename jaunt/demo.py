"""A walkthrough of Jaunt's rules, printed step by step.

    python -m jaunt demo

It uses a throwaway database and a pretend clock, so it gives the same result
every time and never touches real data.
"""

import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from . import services
from .catalogue import seed_catalogue
from .db import create_tables, make_engine, session_factory
from .models import CarClass, Flight, RoomType, Tour, TourSlot
from .money import fmt
from .services import JauntError

PASSWORD = "Demo-password-2026"  # noqa: S105 - throwaway demo account
T0 = datetime(2026, 11, 2, 10, 0)


def run(out=print):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        engine = make_engine(f"sqlite:///{Path(folder) / 'demo.db'}")
        create_tables(engine)
        db = session_factory(engine)()
        try:
            _walkthrough(db, out)
        finally:
            db.close()
            engine.dispose()


def _walkthrough(db, out):
    def step(title):
        out("")
        out(f"== {title} " + "=" * max(0, 66 - len(title)))

    def refused(action):
        try:
            action()
            out("   !! expected this to be refused")
        except JauntError as e:
            out(f"   REFUSED: {e}")

    places = seed_catalogue(db, date(2026, 11, 1), days=40)
    db.commit()
    bqu = places["BQU"].id
    jo = services.create_user(db, "jo@example.test", "Jo", PASSWORD, now=T0)
    kemar = services.create_user(db, "kemar@example.test", "Kemar", PASSWORD, now=T0)

    step("Jo plans a trip (Mon 2 Nov, 10:00)")
    trip = services.create_trip(db, jo, "Bequia birthday week", bqu, date(2026, 11, 10), date(2026, 11, 14),
                                travellers=2, budget_cents=200000, now=T0)
    out(f"   \"{trip.title}\": {trip.place.name}, 10-14 Nov, {trip.travellers} travellers, "
        f"budget {fmt(trip.budget_cents)}")

    step("Searching stays for 10-14 Nov")
    for o in services.search_stays(db, bqu, date(2026, 11, 10), date(2026, 11, 14), now=T0):
        out(f"   {o.room.stay.name + ', ' + o.room.name:<36} {fmt(o.room.nightly_cents):>10}/night  "
            f"{o.nights} nights {fmt(o.total_cents):>12}   {o.rooms_left} left")

    villa = db.scalar(select(RoomType).where(RoomType.name == "Two-bed Villa"))
    step("Rule: a hold sets the room aside for 15 minutes")
    hold = services.hold_stay(db, jo, trip.id, villa.id, date(2026, 11, 10), date(2026, 11, 14), now=T0)
    out(f"   Jo holds the Two-bed Villa: {hold.reference}, {fmt(hold.total_cents)}, held until {hold.held_until:%H:%M}")
    other = services.create_trip(db, kemar, "Kemar in Bequia", bqu, date(2026, 11, 10), date(2026, 11, 14), now=T0)
    out("   10:05 Kemar tries to book the same villa:")
    refused(lambda: services.hold_stay(db, kemar, other.id, villa.id, date(2026, 11, 11), date(2026, 11, 13),
                                       now=T0 + timedelta(minutes=5)))

    step("Rule: an unconfirmed hold lapses and the room is released")
    later = T0 + timedelta(minutes=16)
    k = services.hold_stay(db, kemar, other.id, villa.id, date(2026, 11, 11), date(2026, 11, 13), now=later)
    services.confirm(db, kemar, k.id, now=later)
    out(f"   10:16 Jo's hold has lapsed; Kemar books the villa ({k.reference})")
    out("   Jo comes back to confirm:")
    refused(lambda: services.confirm(db, jo, hold.id, now=later))

    step("Jo books other things instead")
    inn = db.scalar(select(RoomType).where(RoomType.name == "Harbour Double"))
    bookings = [
        services.hold_stay(db, jo, trip.id, inn.id, date(2026, 11, 10), date(2026, 11, 14), now=later),
        services.hold_flight(db, jo, trip.id, db.scalar(select(Flight).where(
            Flight.number == "IH 11", Flight.departs_at == datetime(2026, 11, 10, 8, 0))).id, 2, now=later),
        services.hold_tour(db, jo, trip.id, db.scalar(select(TourSlot).join(Tour).where(
            Tour.name == "Sunset Sail Around Bequia", TourSlot.starts_at == datetime(2026, 11, 11, 16, 30))).id,
            2, now=later),
        services.hold_car(db, jo, trip.id, db.scalar(select(CarClass).where(CarClass.name == "Mini Moke")).id,
                          date(2026, 11, 12), date(2026, 11, 13), now=later),
    ]
    for b in bookings:
        services.confirm(db, jo, b.id, now=later + timedelta(minutes=2))
        out(f"   {b.reference}  {b.title:<40} {fmt(b.total_cents):>12}")
    services.add_item(db, jo, trip.id, date(2026, 11, 12), "Lunch at the Fig Tree", kind="food", time_text="12:30")
    services.add_item(db, jo, trip.id, date(2026, 11, 12), "Princess Margaret Beach", time_text="10:00")

    step("The itinerary (bookings appear by themselves)")
    for day, items in services.itinerary(db, jo, trip.id).items():
        out(f"   {day:%a %d %b}")
        for i in items or []:
            out(f"      {i.time or '     '}  {i.title}")
        if not items:
            out("             (free day)")

    step("Budget")
    b = services.budget(db, jo, trip.id, now=later)
    out(f"   Budget {fmt(b.budget_cents)}, booked {fmt(b.committed_cents)}, left {fmt(b.remaining_cents)}")

    step("Rule: refunds follow each booking's cancellation policy")
    stay, plane, sail, car = bookings
    for booking, when, label in ((car, datetime(2026, 11, 11, 20, 0), "car, the evening before pick-up"),
                                 (sail, datetime(2026, 11, 11, 14, 30), "sunset sail, 2 hours before"),
                                 (plane, datetime(2026, 11, 5, 9, 0), "flight, 3 days after booking"),
                                 (stay, datetime(2026, 11, 9, 20, 0), "stay, 18 hours before check-in")):
        r = services.refund_for(booking, when, 24)
        out(f"   {label:<36} refund {fmt(r.cents):>10}  ({r.reason})")
