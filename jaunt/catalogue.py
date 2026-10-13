"""The demo travel inventory.

The places are real; every hotel, airline, tour operator and car-hire company is
invented. Prices are in US dollars (cents). Inventory is generated for a range
of dates starting from whatever day the demo is created.
"""

import random
from datetime import datetime, time, timedelta

from .models import CarClass, Flight, Place, RoomType, Stay, Tour, TourSlot

PLACES = [
    ("SVD", "St Vincent", "St Vincent and the Grenadines",
     "Black-sand beaches, a living volcano and the gateway to the Grenadines."),
    ("BQU", "Bequia", "St Vincent and the Grenadines",
     "A small island of boat builders, beach bars and very slow afternoons."),
    ("BGI", "Barbados", "Barbados", "Rum shops, flying fish and the calm west-coast sea."),
    ("UVF", "St Lucia", "St Lucia", "The Pitons, rainforest trails and a drive-in volcano."),
    ("GND", "Grenada", "Grenada", "The spice island: nutmeg, waterfalls and Grand Anse beach."),
]

STAYS = {
    #  name, kind, stars, blurb, free-cancel hours, [(room, sleeps, nightly $, rooms)]
    "SVD": [("Black Sand Guesthouse", "guesthouse", 3, "Family-run, ten minutes from the ferry.", 48,
             [("Garden Room", 2, 95, 6), ("Family Room", 4, 145, 2)]),
            ("Soufrière Ridge Lodge", "hotel", 4, "Rainforest views and a pool on the ridge.", 72,
             [("Ridge King", 2, 189, 8), ("Volcano Suite", 3, 289, 2)])],
    "BQU": [("Seagrape Villas", "villa", 4, "Four villas above Princess Margaret Beach.", 72,
             [("One-bed Villa", 2, 240, 3), ("Two-bed Villa", 4, 380, 1)]),
            ("Harbour Lights Inn", "guesthouse", 3, "Above the bakery on Port Elizabeth's waterfront.", 24,
             [("Harbour Double", 2, 110, 5)])],
    "BGI": [("Coral Cove Hotel", "hotel", 4, "West-coast beach hotel with a turtle-watching jetty.", 48,
             [("Garden Double", 2, 210, 12), ("Beachfront Suite", 3, 395, 3)])],
    "UVF": [("Piton View Cottages", "villa", 4, "Wooden cottages facing the Pitons.", 72,
             [("Cottage", 2, 265, 6)])],
    "GND": [("Nutmeg House", "guesthouse", 3, "A short walk to Grand Anse beach.", 48,
             [("Spice Room", 2, 120, 7), ("Family Room", 4, 175, 2)])],
}

#  (number, airline, from, to, departure, minutes, fare $, seats)
ROUTES = [
    ("BW 101", "Breezeway Air", "BGI", "SVD", time(9, 15), 50, 189, 48),
    ("BW 102", "Breezeway Air", "SVD", "BGI", time(11, 0), 50, 189, 48),
    ("BW 207", "Breezeway Air", "BGI", "SVD", time(16, 30), 50, 169, 48),
    ("BW 208", "Breezeway Air", "SVD", "BGI", time(18, 10), 50, 169, 48),
    ("IH 11", "Island Hopper", "SVD", "BQU", time(8, 0), 15, 79, 9),
    ("IH 12", "Island Hopper", "BQU", "SVD", time(8, 40), 15, 79, 9),
    ("IH 31", "Island Hopper", "SVD", "BQU", time(15, 0), 15, 79, 9),
    ("IH 32", "Island Hopper", "BQU", "SVD", time(15, 40), 15, 79, 9),
    ("BW 310", "Breezeway Air", "SVD", "UVF", time(10, 20), 35, 139, 30),
    ("BW 311", "Breezeway Air", "UVF", "SVD", time(13, 0), 35, 139, 30),
    ("BW 420", "Breezeway Air", "SVD", "GND", time(12, 15), 40, 149, 30),
    ("BW 421", "Breezeway Air", "GND", "SVD", time(14, 30), 40, 149, 30),
]

#  place, name, operator, blurb, minutes, price $, start times, capacity
TOURS = [
    ("SVD", "La Soufrière Volcano Hike", "Ridge & Reef Guides",
     "A steep morning climb to the crater rim of St Vincent's volcano.", 360, 85, [time(6, 30)], 12),
    ("SVD", "Tobago Cays Snorkel Day", "Blue Lagoon Sailing",
     "Catamaran to the Cays: turtles, reefs and a beach barbecue.", 540, 165, [time(7, 30)], 20),
    ("SVD", "Dark View Falls & Botanic Gardens", "Ridge & Reef Guides",
     "Twin waterfalls and the oldest botanic gardens in the Americas.", 240, 65, [time(9, 0), time(13, 30)], 10),
    ("BQU", "Sunset Sail Around Bequia", "Admiralty Bay Charters",
     "Two hours on a wooden sloop as the sun goes down.", 120, 70, [time(16, 30)], 14),
    ("BGI", "Rum Shop & Fish Fry Evening", "Bajan Bites Tours",
     "Oistins fish fry with a guided hop between rum shops.", 210, 75, [time(17, 30)], 16),
    ("UVF", "Gros Piton Climb", "Piton Path Guides", "A four-hour guided climb with island-wide views.",
     300, 95, [time(7, 0)], 8),
    ("GND", "Spice Estate & Waterfall Tour", "Grand Anse Adventures",
     "Nutmeg and cocoa estates, then a swim at Concord Falls.", 300, 80, [time(8, 30)], 15),
]

#  place, company, name, seats, daily $, cars
CARS = [
    ("SVD", "Kingstown Car Rentals", "Compact", 4, 55, 6),
    ("SVD", "Kingstown Car Rentals", "4x4 Jeep", 5, 85, 3),
    ("BQU", "Bequia Wheels", "Mini Moke", 4, 70, 2),
    ("BGI", "Bajan Drive", "Economy", 5, 60, 10),
    ("UVF", "Piton Rentals", "4x4", 5, 90, 4),
    ("GND", "Spice Isle Cars", "Compact", 5, 58, 5),
]


def seed_catalogue(db, start, days=120, seed=7):
    """Create places, stays, flights, tours and cars, with dates from `start` for `days` days."""
    rng = random.Random(seed)
    places = {code: Place(code=code, name=name, country=country, blurb=blurb)
              for code, name, country, blurb in PLACES}
    db.add_all(places.values())
    db.flush()

    for code, stays in STAYS.items():
        for name, kind, stars, blurb, hours, rooms in stays:
            stay = Stay(place_id=places[code].id, name=name, kind=kind, stars=stars, blurb=blurb,
                        free_cancel_hours=hours)
            db.add(stay)
            db.flush()
            for room, sleeps, dollars, count in rooms:
                db.add(RoomType(stay_id=stay.id, name=room, sleeps=sleeps, nightly_cents=dollars * 100,
                                rooms_total=count))

    for n in range(days):
        day = start + timedelta(days=n)
        for number, airline, a, b, dep, minutes, dollars, seats in ROUTES:
            departs = datetime.combine(day, dep)
            fare = dollars * 100 + rng.choice([0, 0, 0, 1000, 2000, -1000]) if dollars > 100 else dollars * 100
            db.add(Flight(number=number, airline=airline, origin_id=places[a].id, destination_id=places[b].id,
                          departs_at=departs, arrives_at=departs + timedelta(minutes=minutes),
                          fare_cents=fare, seats_total=seats))

    for code, name, operator, blurb, minutes, dollars, starts, capacity in TOURS:
        tour = Tour(place_id=places[code].id, name=name, operator=operator, blurb=blurb, minutes=minutes,
                    price_cents=dollars * 100)
        db.add(tour)
        db.flush()
        for n in range(days):
            day = start + timedelta(days=n)
            if name.startswith("Tobago") and day.weekday() == 6:      # the catamaran rests on Sundays
                continue
            for t in starts:
                db.add(TourSlot(tour_id=tour.id, starts_at=datetime.combine(day, t), capacity=capacity))

    for code, company, name, seats, dollars, count in CARS:
        db.add(CarClass(place_id=places[code].id, company=company, name=name, seats=seats,
                        daily_cents=dollars * 100, cars_total=count))
    db.flush()
    return places
