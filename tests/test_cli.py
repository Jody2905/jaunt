from datetime import date

from sqlalchemy import func, select

from jaunt import __main__ as cli
from jaunt import demo, services
from jaunt.db import make_engine, session_factory
from jaunt.models import Booking, Trip


def test_demo_walkthrough_runs_and_every_refusal_happens():
    lines = []
    demo.run(out=lines.append)
    text = "\n".join(lines)
    assert "!!" not in text
    assert text.count("REFUSED") == 2
    assert "Check in: Harbour Lights Inn" in text


def test_demo_itinerary_reads_in_time_order():
    lines = []
    demo.run(out=lines.append)
    day = lines[lines.index("   Tue 10 Nov") + 1:lines.index("   Tue 10 Nov") + 3]
    assert [line.split()[0] for line in day] == ["08:00", "14:00"]                  # flight before check-in


def test_init_demo_builds_a_usable_database(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 'jaunt.db'}"
    assert cli.init_demo(url, today=date(2026, 11, 2)) == 0
    engine = make_engine(url)
    db = session_factory(engine)()
    try:
        jo = services.authenticate(db, "jo@example.test", cli.DEMO_PASSWORD)
        assert [t.title for t in services.trips_for(db, jo)] == ["Bequia birthday week", "Grenada spice weekend"]
        assert db.scalar(select(func.count()).select_from(Booking).where(Booking.status == "confirmed")) == 1
        bequia = services.trips_for(db, jo)[0]
        first_full_day = services.itinerary(db, jo, bequia.id)[date(2026, 11, 17)]
        assert [i.time for i in first_full_day] == ["10:00", "13:00"]
    finally:
        db.close()
        engine.dispose()

    assert cli.init_demo(url, today=date(2026, 11, 2)) == 1                   # won't wipe data by accident
    assert "--reset" in capsys.readouterr().err
    assert cli.init_demo(url, reset=True, today=date(2026, 11, 2)) == 0
    engine = make_engine(url)
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(Trip)).scalar() == 2  # fresh, not doubled
    engine.dispose()
