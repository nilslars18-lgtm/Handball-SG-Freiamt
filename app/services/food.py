"""Essen nach dem Spiel: Restaurants in der Naehe der Halle suchen.

- Mit GOOGLE_MAPS_API_KEY: Google Places (mit Bewertung, sortiert nach Bewertung).
- Ohne Key: OpenStreetMap (Nominatim + Overpass, gratis, ohne Bewertungen,
  sortiert nach Distanz zur Halle).
"""
import logging
import math
import re
from typing import List, Optional
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger("food")
UA = {"User-Agent": "SG-Freiamt-U19-TeamApp/1.0 (handball-h3dt.onrender.com)"}

# Umfrage-Option -> OSM-Kuechen (cuisine=...) und Google-Suchwort
CUISINES = {
    "asiatisch": ("asian|chinese|thai|japanese|sushi|vietnamese|korean|indian|ramen|noodle", "Asiatisches Restaurant"),
    "pizza": ("pizza|italian", "Pizzeria"),
    "burger": ("burger|american", "Burger Restaurant"),
    "kebab": ("kebab|turkish|doner|döner", "Kebab"),
}


def _key(label: str) -> Optional[str]:
    l = (label or "").strip().lower()
    for k in CUISINES:
        if k in l:
            return k
    return None


def maps_link(name: str, address: Optional[str]) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote(f"{name} {address or ''}".strip())


def _dist_km(a, b) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    p = math.pi / 180
    h = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(h))


def _geocode(near: str) -> Optional[tuple]:
    """Halle -> Koordinaten. Versucht den ganzen Namen, dann nur den Ort (erstes Wort)."""
    tries = [near]
    first = re.split(r"[ ,]", near.strip())[0]
    if first and first != near:
        tries.append(first)
    for q in tries:
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search", headers=UA, timeout=10,
                          params={"q": f"{q}, Schweiz", "format": "json", "limit": 1, "countrycodes": "ch"})
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception as e:  # noqa: BLE001
            log.warning("geocode %s: %s", q, e)
    return None


def _osm(label: str, near: str, limit: int) -> List[dict]:
    k = _key(label)
    pos = _geocode(near)
    if not pos:
        return []
    regex = CUISINES[k][0] if k else None
    flt = f'["cuisine"~"{regex}",i]' if regex else ""
    radius = 4000
    q = f"""[out:json][timeout:20];
(node["amenity"~"restaurant|fast_food"]{flt}(around:{radius},{pos[0]},{pos[1]});
 way["amenity"~"restaurant|fast_food"]{flt}(around:{radius},{pos[0]},{pos[1]}););
out center tags;"""
    try:
        r = httpx.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers=UA, timeout=25)
        els = r.json().get("elements", [])
    except Exception as e:  # noqa: BLE001
        log.warning("overpass: %s", e)
        return []
    out = []
    for e in els:
        t = e.get("tags", {})
        name = t.get("name")
        if not name:
            continue
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        addr = " ".join(x for x in [t.get("addr:street", ""), t.get("addr:housenumber", "")] if x).strip()
        city = t.get("addr:city") or ""
        address = ", ".join(x for x in [addr, city] if x) or None
        out.append({"name": name, "address": address, "rating": None, "ratings": None,
                    "distance_km": round(_dist_km(pos, (lat, lon)), 1),
                    "restaurant": t.get("amenity") == "restaurant", "maps_link": maps_link(name, address or city)})
    # richtige Restaurants vor Imbiss, dann nach Distanz
    out.sort(key=lambda x: (not x["restaurant"], x["distance_km"]))
    return out[:limit]


def _google(label: str, near: str, limit: int) -> List[dict]:
    k = _key(label)
    query = f"{CUISINES[k][1] if k else label + ' Restaurant'} in der Nähe von {near}, Schweiz"
    try:
        r = httpx.get("https://maps.googleapis.com/maps/api/place/textsearch/json", timeout=10,
                      params={"query": query, "region": "ch", "language": "de", "key": settings.google_maps_api_key})
        res = r.json().get("results") or []
    except Exception as e:  # noqa: BLE001
        log.warning("google places: %s", e)
        return []
    out = [{"name": x.get("name"), "address": x.get("formatted_address"), "rating": x.get("rating"),
            "ratings": x.get("user_ratings_total"), "distance_km": None, "restaurant": True,
            "maps_link": maps_link(x.get("name", ""), x.get("formatted_address"))}
           for x in res if x.get("business_status", "OPERATIONAL") == "OPERATIONAL"]
    # gute Bewertung mit genug Stimmen zuerst
    out.sort(key=lambda x: -((x["rating"] or 0) * min(1.0, (x["ratings"] or 0) / 50)))
    return out[:limit]


def find_restaurants(label: str, near: str, limit: int = 3) -> List[dict]:
    if not near:
        return []
    if settings.google_maps_api_key:
        found = _google(label, near, limit)
        if found:
            return found
    return _osm(label, near, limit)


def short(r: dict) -> str:
    extra = f" ⭐{r['rating']}" if r.get("rating") else (f" ({r['distance_km']} km)" if r.get("distance_km") is not None else "")
    return f"{r['name']}{extra}"
