"""Command line.

  python -m jaunt demo                 walk through the booking rules with a throwaway database
  python -m jaunt init-demo [--reset]  build the database (via migrations) and fill it with demo data
  python -m jaunt serve [--port 8000]  run the app; open http://127.0.0.1:8000/
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import demo
from .db import database_url

DEMO_PASSWORD = "Jaunt-2026-demo"
ROOT = Path(__file__).resolve().parent.parent


def migrate(url):
    """Bring the database up to the latest migration."""
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


def init_demo(url, reset=False, today=None):
    from sqlalchemy import func, inspect, select

    from . import services
    from .catalogue import seed_catalogue
    from .db import make_engine, session_factory
    from .models import Base, Place

    engine = make_engine(url)
    if inspect(engine).has_table("places"):
        with engine.connect() as c:
            has_data = c.execute(select(func.count()).select_from(Place)).scalar() > 0
        if has_data and not reset:
            engine.dispose()
            print("The database already has data. Use --reset to start again (this deletes it).", file=sys.stderr)
            return 1
        Base.metadata.drop_all(engine)
        with engine.begin() as c:
            c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    engine.dispose()
    migrate(url)

    today = today or date.today()
    now = datetime.combine(today, datetime.min.time()).replace(hour=9)
    engine = make_engine(url)
    db = session_factory(engine)()
    places = seed_catalogue(db, today, days=120)
    db.commit()
    jo = services.create_user(db, "jo@example.test", "Jo", DEMO_PASSWORD, now=now)
    services.create_user(db, "kemar@example.test", "Kemar", DEMO_PASSWORD, now=now)
    start = today + timedelta(days=14)
    trip = services.create_trip(db, jo, "Bequia birthday week", places["BQU"].id, start, start + timedelta(days=5),
                                travellers=2, budget_cents=250000, now=now)
    inn = services.search_stays(db, places["BQU"].id, start, start + timedelta(days=5), now=now)[0]
    services.confirm(db, jo, services.hold_stay(db, jo, trip.id, inn.room.id, start, start + timedelta(days=5),
                                                now=now).id, now=now)
    services.add_item(db, jo, trip.id, start + timedelta(days=1), "Princess Margaret Beach", time_text="10:00")
    services.add_item(db, jo, trip.id, start + timedelta(days=1), "Lunch with a sea view", kind="food",
                      time_text="13:00")
    services.create_trip(db, jo, "Grenada spice weekend", places["GND"].id, today + timedelta(days=40),
                         today + timedelta(days=42), travellers=1, budget_cents=90000, now=now)
    db.close()
    engine.dispose()

    print("Database ready (built with Alembic migrations).\n")
    print(f"Demo logins (password for both: {DEMO_PASSWORD})")
    print("  jo@example.test      has two trips planned")
    print("  kemar@example.test   no trips yet")
    print(f"\nTravel inventory: 5 islands, 120 days from {today:%d %b %Y}.")
    return 0


def serve(url, host, port):
    import uvicorn
    from sqlalchemy import inspect

    from .api import create_app
    from .db import make_engine

    engine = make_engine(url)
    if not inspect(engine).has_table("places"):
        print("There's no Jaunt database here yet. Run:  py -m jaunt init-demo", file=sys.stderr)
        return 1
    engine.dispose()
    migrate(url)                                   # pick up any new migrations
    print(f"Jaunt is running. Open http://{host}:{port}/  (API docs at /docs; Ctrl+C to stop)")
    uvicorn.run(create_app(url), host=host, port=port, log_level="warning")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jaunt")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="walk through the booking rules step by step")
    i = sub.add_parser("init-demo", help="build the database and fill it with demo data")
    i.add_argument("--db", help="SQLite file or database URL (default: $JAUNT_DATABASE_URL or jaunt.db)")
    i.add_argument("--reset", action="store_true", help="delete existing data first")
    v = sub.add_parser("serve", help="run the web API")
    v.add_argument("--db", help="SQLite file or database URL")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "demo":
        demo.run()
        return 0
    if args.command == "serve":
        return serve(database_url(args.db), args.host, args.port)
    return init_demo(database_url(args.db), args.reset)


if __name__ == "__main__":
    sys.exit(main())
