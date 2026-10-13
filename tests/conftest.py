import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jaunt import services  # noqa: E402
from jaunt.catalogue import seed_catalogue  # noqa: E402
from jaunt.db import create_tables, make_engine, session_factory  # noqa: E402
from jaunt.models import Base, CarClass, Flight, Place, RoomType, TourSlot  # noqa: E402
from sqlalchemy import select  # noqa: E402

PASSWORD = "correct-horse-battery"
NOW = datetime(2026, 11, 2, 10, 0)            # "now" in every test: Monday 2 November, 10:00
TEST_DATABASE_URL = os.environ.get("JAUNT_TEST_DATABASE_URL")


def d(day, month=11):
    return date(2026, month, day)


@pytest.fixture
def db_url(tmp_path):
    if not TEST_DATABASE_URL:
        return f"sqlite:///{tmp_path / 'test.db'}"
    engine = make_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return TEST_DATABASE_URL


@pytest.fixture
def engine(db_url):
    engine = make_engine(db_url)
    create_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def Session(engine):
    return session_factory(engine)


@pytest.fixture
def db(Session):
    session = Session()
    yield session
    session.close()


@pytest.fixture
def places(db):
    """The demo catalogue, with dates from 1 November for 60 days."""
    p = seed_catalogue(db, d(1), days=60)
    db.commit()
    return {code: place.id for code, place in p.items()}


@pytest.fixture
def jo(db):
    return services.create_user(db, "jo@example.test", "Jo", PASSWORD, now=NOW)


@pytest.fixture
def friend(db):
    return services.create_user(db, "friend@example.test", "Kemar", PASSWORD, now=NOW)


@pytest.fixture
def trip(db, jo, places):
    """Jo's Bequia week: 10-16 November, two travellers, US$3,000 budget."""
    return services.create_trip(db, jo, "Bequia birthday week", places["BQU"], d(10), d(16), travellers=2,
                                budget_cents=300000, now=NOW)


def room(db, stay_name, room_name):
    return db.scalar(select(RoomType).join(RoomType.stay).where(RoomType.name == room_name)
                     .where(RoomType.stay.has(name=stay_name)))


def flight(db, number, day):
    return db.scalar(select(Flight).where(Flight.number == number, Flight.departs_at >= datetime.combine(day, datetime.min.time()),
                                          Flight.departs_at < datetime.combine(day + timedelta(days=1), datetime.min.time())))


def slot(db, tour_name, day):
    return db.scalar(select(TourSlot).where(TourSlot.tour.has(name=tour_name),
                                            TourSlot.starts_at >= datetime.combine(day, datetime.min.time()),
                                            TourSlot.starts_at < datetime.combine(day + timedelta(days=1), datetime.min.time())))


def car(db, company, name):
    return db.scalar(select(CarClass).where(CarClass.company == company, CarClass.name == name))
