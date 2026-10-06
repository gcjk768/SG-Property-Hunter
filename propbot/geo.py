"""Location signals: distance to the nearest MRT exit and upcoming stations by name.

Station exits come from LTA's GeoJSON on data.gov.sg (free, no key, loaded once a day). Addresses are
geocoded with OneMap, which needs ONEMAP_EMAIL and ONEMAP_PASSWORD; without them every call returns
nothing and the cards simply have no location line. Upcoming stations come from rules/mrt_pipeline.yaml.
Everything here is best effort: an error is logged and the card goes out without the line.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import date, datetime

import httpx
import yaml

from .config import Settings
from .db import DB
from .ratelimit import RateLimiter

log = logging.getLogger("propbot.geo")

POLL_DOWNLOAD = "https://api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download"
ONEMAP_TOKEN = "https://www.onemap.gov.sg/api/auth/post/getToken"
ONEMAP_SEARCH = "https://www.onemap.gov.sg/api/common/elastic/search"
TOKEN_LIFE_S = 2 * 24 * 3600          # OneMap tokens last 3 days; renew after 2


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def station_label(name: str) -> str:
    """'SPRINGLEAF MRT STATION' -> 'Springleaf'."""
    return re.sub(r"\s+(MRT|LRT)\s+STATION.*$", "", name.strip(), flags=re.I).title()


def parse_exits(geojson: dict) -> list[tuple[str, str, float, float]]:
    rows = []
    for f in geojson.get("features", []):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        if g.get("type") != "Point" or not p.get("STATION_NA"):
            continue
        lon, lat = g["coordinates"][:2]
        rows.append((p["STATION_NA"], p.get("EXIT_CODE") or "", float(lat), float(lon)))
    return rows


class Geo:
    def __init__(self, db: DB, settings: Settings, limiter: RateLimiter, http: httpx.Client, pipeline_path=None):
        self.db, self.s, self.limiter, self.http = db, settings, limiter, http
        self._pipeline = pipeline_path or settings.rules_dir / "mrt_pipeline.yaml"
        self._upcoming: list[dict] | None = None
        self._radius = 800

    # ------------------------------------------------------------ station exits
    def ensure_stations(self, today: date) -> bool:
        """Load LTA's exits once a day. Returns whether any exits are stored."""
        ds = self.s.sources.datagov.datasets.get("mrt_exits")
        if ds and self.db.meta_get("mrt_exits_day") != today.isoformat():
            try:
                self.limiter.acquire("datagov")
                meta = self.http.get(POLL_DOWNLOAD.format(id=ds), timeout=30).json()
                url = (meta.get("data") or {}).get("url")
                self.limiter.acquire("datagov")
                rows = parse_exits(self.http.get(url, timeout=120).json()) if url else []
                if rows:
                    self.db.executemany("INSERT OR REPLACE INTO mrt_exits VALUES (?,?,?,?)", rows)
                self.db.meta_set("mrt_exits_day", today.isoformat())
            except Exception as exc:         # best effort: the card goes out without the line
                log.warning("MRT exits not loaded: %s", exc)
        return bool(self.db.scalar("SELECT COUNT(*) FROM mrt_exits", default=0))

    def nearest(self, lat: float, lon: float) -> tuple[str, float] | None:
        # ponytail: a scan over ~600 exits per lookup; index by grid if it ever shows in a profile
        best = None
        for r in self.db.all("SELECT station, lat, lon FROM mrt_exits"):
            d = haversine_m(lat, lon, r["lat"], r["lon"])
            if best is None or d < best[1]:
                best = (station_label(r["station"]), d)
        return best

    # ------------------------------------------------------------ OneMap
    def _token(self) -> str | None:
        sec = self.s.secrets
        if not (sec.onemap_email and sec.onemap_password):
            return None
        exp = float(self.db.meta_get("onemap_token_exp") or 0)
        tok = self.db.meta_get("onemap_token")
        if tok and time.time() < exp:
            return tok
        self.limiter.acquire("onemap")
        resp = self.http.post(ONEMAP_TOKEN, json={"email": sec.onemap_email, "password": sec.onemap_password},
                              timeout=30)
        resp.raise_for_status()
        tok = resp.json().get("access_token")
        if tok:
            self.db.meta_set("onemap_token", tok)
            self.db.meta_set("onemap_token_exp", str(time.time() + TOKEN_LIFE_S))
        return tok

    def geocode(self, query: str) -> tuple[float, float] | None:
        q = re.sub(r"\s+", " ", query).strip().upper()
        if not q:
            return None
        row = self.db.one("SELECT json FROM onemap_cache WHERE query=?", (q,))
        if row:
            hit = json.loads(row["json"])
            return tuple(hit) if hit else None
        tok = self._token()
        if not tok:
            return None
        self.limiter.acquire("onemap")
        resp = self.http.get(ONEMAP_SEARCH, params={"searchVal": q, "returnGeom": "Y", "getAddrDetails": "N",
                                                    "pageNum": 1}, headers={"Authorization": tok}, timeout=30)
        self.limiter.log("onemap", "search", url=str(resp.request.url), status=resp.status_code,
                         nbytes=len(resp.content))
        resp.raise_for_status()
        results = resp.json().get("results") or []
        hit = (float(results[0]["LATITUDE"]), float(results[0]["LONGITUDE"])) if results else None
        self.db.upsert("onemap_cache", {"query": q, "fetched_at": datetime.now().isoformat(timespec="seconds"),
                                        "json": json.dumps(hit)}, "query")
        return hit

    # ------------------------------------------------------------ upcoming stations
    def upcoming(self, *texts: str) -> list[str]:
        """Planned stations whose name appears in the address or town, or whose name contains the town.
        ponytail: a name match, not a distance; LTA has not published coordinates for most planned stations."""
        if self._upcoming is None:
            try:
                data = yaml.safe_load(self._pipeline.read_text(encoding="utf-8")) or {}
                self._upcoming = [s for s in data.get("stations", []) if s.get("status") in ("upcoming", "due")]
                self._radius = int((data.get("meta") or {}).get("signal_radius_m") or 800)
            except (OSError, yaml.YAMLError) as exc:
                log.warning("mrt_pipeline.yaml not read: %s", exc)
                self._upcoming = []
        blob = " ".join(t for t in texts if t).lower()
        towns = [t.lower() for t in texts if t and len(t) >= 4]
        out = []
        for st in self._upcoming:
            name = str(st.get("name", "")).lower()
            if name and (name in blob or any(t in name for t in towns)):
                out.append(f"{st['name']} ({st.get('line', '')}, {st.get('opening_year', '')})")
        return out[:2]

    # ------------------------------------------------------------ the card line
    def describe(self, address: str, area: str = "", today: date | None = None) -> str:
        """'🟢 Khatib MRT 420 m · upcoming Hougang (Cross Island Line, 2030)'; empty when nothing is known."""
        parts = []
        try:
            if self.ensure_stations(today or date.today()):
                hit = self.geocode(f"{address} Singapore") if address else None
                near = self.nearest(*hit) if hit else None
                if near:
                    mark = "🟢 " if near[1] <= self._radius else ""
                    parts.append(f"{mark}{near[0]} MRT {near[1]:,.0f} m")
        except Exception as exc:
            log.warning("location lookup failed for %r: %s", address, exc)
        up = self.upcoming(address, area)
        if up:
            parts.append("upcoming " + ", ".join(up))
        return " · ".join(parts)
