"""The web API, tested through real HTTP requests (FastAPI's TestClient)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jaunt.api import COOKIE, create_app
from jaunt.models import LoginSession

from . import conftest
from .conftest import NOW, PASSWORD, d


def _id_of(helper):
    """Look something up, then end the test's read so the API (another connection) can write."""
    def lookup(db, *args):
        found = helper(db, *args).id
        db.rollback()
        return found
    return lookup


room, flight, slot, car = (_id_of(h) for h in (conftest.room, conftest.flight, conftest.slot, conftest.car))


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(engine, clock):
    return create_app(engine=engine, clock=clock)


@pytest.fixture
def client(app, places):
    with TestClient(app) as c:
        yield c


def log_in(client, email="jo@example.test", password=PASSWORD):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


@pytest.fixture
def jo_client(client, jo):
    log_in(client)
    return client


@pytest.fixture
def trip_id(jo_client, places):
    r = jo_client.post("/api/trips", json={"title": "Bequia birthday week", "place_id": places["BQU"],
                                           "start_date": "2026-11-10", "end_date": "2026-11-16",
                                           "travellers": 2, "budget_cents": 300000})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---- Logging in -------------------------------------------------------------------------------

def test_register_logs_you_in_with_a_safe_cookie(client):
    r = client.post("/api/auth/register", json={"email": "Sam@Example.test", "name": "Sam",
                                                "password": "a-long-passphrase"})
    assert r.status_code == 201, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=604800" in cookie
    me = client.get("/api/me").json()
    assert me["user"]["email"] == "sam@example.test"
    assert me["csrf_token"] == r.json()["csrf_token"]
    again = client.post("/api/auth/register", json={"email": "sam@example.test", "name": "Sam 2",
                                                    "password": "a-long-passphrase"})
    assert (again.status_code, again.json()["detail"]) == (400, "There's already an account with this email")


def test_only_a_hash_of_the_cookie_is_stored(client, jo, db):
    log_in(client)
    token = client.cookies[COOKIE]
    stored = db.scalars(select(LoginSession.token_hash)).all()
    assert len(stored) == 1 and token not in stored[0] and len(stored[0]) == 64


def test_wrong_password_gives_the_same_answer_as_unknown_email(client, jo):
    a = client.post("/api/auth/login", json={"email": "jo@example.test", "password": "wrong-password"})
    b = client.post("/api/auth/login", json={"email": "nobody@example.test", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "Wrong email or password"}


def test_five_wrong_passwords_pause_logins_for_15_minutes(client, jo, clock):
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "jo@example.test", "password": "wrong-password"})
    r = client.post("/api/auth/login", json={"email": "JO@example.test", "password": PASSWORD})
    assert r.status_code == 429 and int(r.headers["retry-after"]) == 15 * 60
    clock.advance(minutes=15, seconds=1)
    log_in(client)                                              # the right password works again


def test_pages_need_a_login(client):
    for method, path in (("get", "/api/trips"), ("get", "/api/me"), ("post", "/api/trips"),
                         ("post", "/api/bookings/1/confirm")):
        r = getattr(client, method)(path)
        assert (r.status_code, r.json()["detail"]) == (401, "Please log in"), path


def test_changes_need_the_csrf_token(jo_client, places):
    good = jo_client.headers.pop("X-CSRF-Token")
    body = {"title": "Grenada", "place_id": places["GND"], "start_date": "2026-11-20", "end_date": "2026-11-22"}
    assert jo_client.post("/api/trips", json=body).status_code == 403
    assert jo_client.post("/api/trips", json=body, headers={"X-CSRF-Token": "guess"}).status_code == 403
    assert jo_client.get("/api/trips").status_code == 200                 # reading doesn't need it
    assert jo_client.post("/api/trips", json=body, headers={"X-CSRF-Token": good}).status_code == 201


def test_logout_kills_the_session_even_for_a_copied_cookie(jo_client):
    stolen = jo_client.cookies[COOKIE]
    assert jo_client.post("/api/auth/logout").status_code == 204
    jo_client.cookies.set(COOKIE, stolen)
    assert jo_client.get("/api/me").status_code == 401


def test_logging_in_again_replaces_the_old_session(jo_client):
    old = jo_client.cookies[COOKIE]
    log_in(jo_client)
    assert jo_client.cookies[COOKIE] != old
    jo_client.cookies.set(COOKIE, old)
    assert jo_client.get("/api/me").status_code == 401


def test_sessions_last_seven_days(jo_client, clock):
    clock.advance(days=6, hours=23)
    assert jo_client.get("/api/me").status_code == 200
    clock.advance(hours=1)
    assert jo_client.get("/api/me").status_code == 401


# ---- Requests are checked ---------------------------------------------------------------------------

@pytest.mark.parametrize("change, message", [
    ({"travellers": "2"}, "travellers: Input should be a valid integer"),      # strings aren't numbers
    ({"travellers": True}, "travellers: Input should be a valid integer"),
    ({"start_date": "next tuesday"}, "start_date: Input should be a valid date"),
    ({"surprise": 1}, "surprise: Extra inputs are not permitted"),
])
def test_bad_input_gets_a_readable_422(jo_client, places, change, message):
    body = {"title": "Trip", "place_id": places["GND"], "start_date": "2026-11-20", "end_date": "2026-11-22",
            **change}
    r = jo_client.post("/api/trips", json=body)
    assert r.status_code == 422 and message in r.json()["detail"], r.text


def test_rule_breaks_come_back_as_400_with_the_reason(jo_client, places):
    r = jo_client.post("/api/trips", json={"title": "Trip", "place_id": places["GND"],
                                           "start_date": "2026-11-20", "end_date": "2026-11-18"})
    assert (r.status_code, r.json()["detail"]) == (400, "The trip must end on or after the day it starts")


def test_security_headers(jo_client):
    h = jo_client.get("/api/trips").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store"


# ---- Trips and the itinerary ---------------------------------------------------------------------

def test_trip_comes_back_with_every_day_and_its_budget(jo_client, trip_id):
    t = jo_client.get(f"/api/trips/{trip_id}").json()
    assert t["place"]["code"] == "BQU" and t["days"] == 7
    assert [day["date"] for day in t["itinerary"]] == [f"2026-11-{n}" for n in range(10, 17)]
    assert t["budget"] == {"budget_cents": 300000, "committed_cents": 0, "on_hold_cents": 0,
                           "remaining_cents": 300000, "over_budget": False}


def test_itinerary_editing(jo_client, trip_id):
    url = f"/api/trips/{trip_id}/items"
    jo_client.post(url, json={"day": "2026-11-12", "title": "Lunch", "kind": "food", "time": "12:30"})
    t = jo_client.post(url, json={"day": "2026-11-12", "title": "Beach", "time": "9:30"}).json()
    day = t["itinerary"][2]["items"]
    assert [(i["title"], i["time"]) for i in day] == [("Beach", "09:30"), ("Lunch", "12:30")]
    lunch = day[1]["id"]
    t = jo_client.post(f"/api/items/{lunch}/move", json={"day": "2026-11-13", "position": 0}).json()
    assert [i["title"] for i in t["itinerary"][3]["items"]] == ["Lunch"]
    t = jo_client.patch(f"/api/items/{lunch}", json={"notes": "Book a table by the water"}).json()
    assert t["itinerary"][3]["items"][0]["notes"] == "Book a table by the water"
    t = jo_client.delete(f"/api/items/{lunch}").json()
    assert t["itinerary"][3]["items"] == []
    r = jo_client.patch(f"/api/items/{lunch}", json={"notes": "gone?"})
    assert r.status_code == 404


def test_someone_elses_trip_and_bookings_look_missing(client, jo, friend, db, places):
    log_in(client)
    trip = client.post("/api/trips", json={"title": "Jo's", "place_id": places["BQU"],
                                           "start_date": "2026-11-10", "end_date": "2026-11-12"}).json()["id"]
    villa = room(db, "Seagrape Villas", "One-bed Villa")
    booking = client.post(f"/api/trips/{trip}/holds/stay", json={"room_type_id": villa, "check_in": "2026-11-10",
                                                                 "check_out": "2026-11-12"}).json()["id"]
    log_in(client, "friend@example.test")
    for method, path, body in (("get", f"/api/trips/{trip}", None), ("delete", f"/api/trips/{trip}", None),
                               ("post", f"/api/bookings/{booking}/confirm", None),
                               ("post", f"/api/bookings/{booking}/cancel", None),
                               ("get", f"/api/bookings/{booking}/refund", None),
                               ("post", f"/api/trips/{trip}/items", {"day": "2026-11-10", "title": "Sneaky"})):
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 404, (path, r.text)
    assert client.get("/api/trips").json() == []


# ---- Searching and booking ----------------------------------------------------------------------------

def test_search_is_open_to_everyone(client, places):
    stays = client.get("/api/search/stays", params={"place_id": places["BQU"], "check_in": "2026-11-10",
                                                    "check_out": "2026-11-13"}).json()
    inn = next(o for o in stays if o["stay"] == "Harbour Lights Inn")
    assert (inn["nights"], inn["nightly_cents"], inn["total_cents"]) == (3, 11000, 33000)
    flights = client.get("/api/search/flights", params={"origin_id": places["SVD"], "destination_id": places["BQU"],
                                                        "day": "2026-11-10", "passengers": 2}).json()
    assert flights and all(f["origin"] == "SVD" and f["total_cents"] == f["fare_cents"] * 2 for f in flights)
    r = client.get("/api/search/stays", params={"place_id": places["BQU"], "check_in": "2026-11-10",
                                                "check_out": "2026-11-10"})
    assert (r.status_code, r.json()["detail"]) == (400, "Check-out must be at least a day after check-in")


def test_the_whole_booking_flow(jo_client, trip_id, db, clock):
    inn = room(db, "Harbour Lights Inn", "Harbour Double")
    hold = jo_client.post(f"/api/trips/{trip_id}/holds/stay",
                          json={"room_type_id": inn, "check_in": "2026-11-10", "check_out": "2026-11-14"})
    assert hold.status_code == 201, hold.text
    b = hold.json()
    assert (b["status"], b["total_cents"], b["held_until"]) == ("held", 44000, "2026-11-02T10:15:00")
    assert b["reference"].startswith("JNT-")
    t = jo_client.get(f"/api/trips/{trip_id}").json()
    assert t["budget"]["on_hold_cents"] == 44000 and t["itinerary"][0]["items"] == []

    clock.advance(minutes=5)
    confirmed = jo_client.post(f"/api/bookings/{b['id']}/confirm").json()
    assert confirmed["status"] == "confirmed"
    t = jo_client.get(f"/api/trips/{trip_id}").json()
    assert t["budget"]["committed_cents"] == 44000 and t["budget"]["on_hold_cents"] == 0
    assert [(i["time"], i["kind"]) for i in t["itinerary"][0]["items"]] == [("14:00", "booking")]
    check_in = t["itinerary"][0]["items"][0]["id"]
    r = jo_client.delete(f"/api/items/{check_in}")
    assert (r.status_code, r.json()["detail"]) == (400, "Cancel the booking to remove it from your plan")

    clock.now = NOW.replace(day=9, hour=20)                    # 18 hours before check-in
    log_in(jo_client)                                          # a week later: the old session has run out
    quote = jo_client.get(f"/api/bookings/{b['id']}/refund").json()
    assert quote["refund_cents"] == 33000 and "first night" in quote["reason"]
    done = jo_client.post(f"/api/bookings/{b['id']}/cancel").json()
    assert (done["booking"]["status"], done["refund_cents"]) == ("cancelled", 33000)
    t = jo_client.get(f"/api/trips/{trip_id}").json()
    assert t["itinerary"][0]["items"] == [] and t["budget"]["committed_cents"] == 11000   # the kept night


def test_flights_tours_and_cars_can_be_held(jo_client, trip_id, db):
    holds = [
        ("flight", {"flight_id": flight(db, "IH 11", d(10)), "passengers": 2}),
        ("tour", {"slot_id": slot(db, "Sunset Sail Around Bequia", d(11)), "people": 2}),
        ("car", {"car_class_id": car(db, "Bequia Wheels", "Mini Moke"), "pickup": "2026-11-12",
                 "dropoff": "2026-11-13"}),
    ]
    for kind, body in holds:
        r = jo_client.post(f"/api/trips/{trip_id}/holds/{kind}", json=body)
        assert r.status_code == 201 and r.json()["kind"] == kind, r.text
    r = jo_client.post(f"/api/trips/{trip_id}/holds/flight", json={**holds[0][1], "passengers": 3})
    assert (r.status_code, r.json()["detail"]) == (400, "You can book between 1 and 2 passengers "
                                                        "(this trip is for 2)")


def test_sold_out_is_a_409(jo_client, trip_id, db):
    villa = room(db, "Seagrape Villas", "Two-bed Villa")                      # only one of these
    body = {"room_type_id": villa, "check_in": "2026-11-10", "check_out": "2026-11-12"}
    assert jo_client.post(f"/api/trips/{trip_id}/holds/stay", json=body).status_code == 201
    r = jo_client.post(f"/api/trips/{trip_id}/holds/stay", json=body)
    assert r.status_code == 409 and "fully booked" in r.json()["detail"]


def test_a_lapsed_hold_reads_as_expired_and_cant_be_confirmed(jo_client, trip_id, db, clock):
    villa = room(db, "Seagrape Villas", "Two-bed Villa")
    b = jo_client.post(f"/api/trips/{trip_id}/holds/stay", json={"room_type_id": villa, "check_in": "2026-11-10",
                                                                 "check_out": "2026-11-12"}).json()
    clock.advance(minutes=15)
    assert jo_client.get(f"/api/trips/{trip_id}").json()["bookings"][0]["status"] == "expired"
    r = jo_client.post(f"/api/bookings/{b['id']}/confirm")
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    again = jo_client.post(f"/api/trips/{trip_id}/holds/stay", json={"room_type_id": villa,
                                                                     "check_in": "2026-11-10",
                                                                     "check_out": "2026-11-12"})
    assert again.status_code == 201                                             # the villa was released


def test_trip_with_live_bookings_cant_be_deleted(jo_client, trip_id, db):
    villa = room(db, "Seagrape Villas", "Two-bed Villa")
    jo_client.post(f"/api/trips/{trip_id}/holds/stay", json={"room_type_id": villa, "check_in": "2026-11-10",
                                                             "check_out": "2026-11-12"})
    r = jo_client.delete(f"/api/trips/{trip_id}")
    assert (r.status_code, r.json()["detail"]) == (400, "Cancel this trip's bookings before deleting it")


# ---- Documentation ---------------------------------------------------------------------------------------

def test_docs_and_openapi(client):
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert {"/api/auth/login", "/api/trips/{trip_id}", "/api/trips/{trip_id}/holds/stay",
            "/api/bookings/{booking_id}/cancel"} <= set(spec["paths"])
    assert {"X-CSRF-Token", COOKIE} <= {v["name"] for v in spec["components"]["securitySchemes"].values()}
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"
