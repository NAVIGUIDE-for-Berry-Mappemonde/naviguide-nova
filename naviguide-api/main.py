import os
import re
import math
import json
import time
import asyncio
from typing import Optional, Union, List
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import httpx
import searoute as sr
from geographiclib.geodesic import Geodesic
from copernicus.getWind import get_wind_data_at_position
from copernicus.getWave import get_wave_data_at_position
from copernicus.getCurrent import get_current_data_at_position
from utils.addWindProperties import add_wind_properties_to_route

try:
    from global_land_mask import globe as _globe
    _LAND_MASK_AVAILABLE = True
except ImportError:
    _globe = None
    _LAND_MASK_AVAILABLE = False

# ── High-resolution land mask (Natural Earth 1:10m + minor islands) ───────────
import pathlib, shapely.geometry, shapely.strtree

_NE_TREE: Optional[shapely.strtree.STRtree] = None

def _load_ne_land_tree() -> Optional[shapely.strtree.STRtree]:
    """
    Load Natural Earth 1:10m land + minor-islands shapefiles and build an
    STRtree for fast point-in-polygon queries.  Returns None on failure.
    """
    try:
        import shapefile as pyshp
        geo_dir = pathlib.Path(__file__).parent / "geo_data"
        polys: list = []
        for fname in ("ne_10m_land.shp", "ne_10m_minor_islands.shp"):
            shp = geo_dir / fname
            if not shp.exists():
                continue
            sf = pyshp.Reader(str(shp))
            for shape in sf.shapes():
                geo = shapely.geometry.shape(shape.__geo_interface__)
                if geo.geom_type == "Polygon":
                    if geo.is_valid and not geo.is_empty:
                        polys.append(geo)
                elif geo.geom_type == "MultiPolygon":
                    for sub in geo.geoms:
                        if sub.is_valid and not sub.is_empty:
                            polys.append(sub)
        if polys:
            print(f"✅ NE land mask loaded: {len(polys)} polygons")
            return shapely.strtree.STRtree(polys)
    except Exception as exc:
        print(f"⚠️  NE land mask unavailable: {exc}")
    return None

_NE_TREE = _load_ne_land_tree()


def _is_land_hires(lat: float, lon: float) -> bool:
    """
    Combined high-resolution land check:
    1. global_land_mask (1/4° grid, very fast) — catches continents/large islands
    2. Natural Earth 1:10m STRtree — catches smaller islands (Nias, Mentawai…)
    Returns True if EITHER source classifies the point as land.
    """
    # Primary: fast 1/4° grid check
    if _LAND_MASK_AVAILABLE:
        try:
            if bool(_globe.is_land(lat, lon)):
                return True
        except Exception:
            pass
    # Secondary: high-resolution shapely tree for small islands
    if _NE_TREE is not None:
        pt = shapely.geometry.Point(lon, lat)
        return len(_NE_TREE.query(pt, predicate="intersects")) > 0
    return False

# Charger les variables d'environnement
load_dotenv()

COPERNICUS_USERNAME = os.getenv("COPERNICUS_USERNAME")
COPERNICUS_PASSWORD = os.getenv("COPERNICUS_PASSWORD")

if not COPERNICUS_USERNAME or not COPERNICUS_PASSWORD:
    print("⚠️  WARNING: Copernicus credentials not set. Wind/wave data will be unavailable.")
else:
    print(f"✅ Copernicus configured for user: {COPERNICUS_USERNAME}")


# ── Land-waypoint sanitiser ───────────────────────────────────────────────────

def _has_nearby_ocean(lat: float, lon: float, radius_deg: float = 1.5) -> bool:
    """
    Return True if there is at least one ocean cell within *radius_deg* of
    (lat, lon). Used to distinguish genuine inland waypoints from narrow
    maritime passages like the Suez Canal, where global_land_mask at 1/4°
    incorrectly labels every canal cell as land even though the point is a
    valid maritime route node.
    """
    if not _LAND_MASK_AVAILABLE:
        return True   # no mask available → assume navigable
    steps = [-radius_deg, -radius_deg * 0.5, 0.0, radius_deg * 0.5, radius_deg]
    for dlat in steps:
        for dlon in steps:
            try:
                if not _globe.is_land(lat + dlat, lon + dlon):
                    return True
            except Exception:
                pass
    return False


def _segment_crosses_land(lon1: float, lat1: float, lon2: float, lat2: float,
                          n_samples: int = 30) -> bool:
    """
    Return True if the great-circle segment from (lon1,lat1)→(lon2,lat2)
    crosses any land, including small islands and archipelagos.

    Uses the combined high-resolution check (_is_land_hires) which queries
    both global_land_mask (1/4° grid) and the Natural Earth 1:10m STRtree,
    so islands like Nias and the Mentawai group are reliably detected.
    Dense sampling (30 pts) catches features as narrow as ~20 km.

    Antimeridian segments (|Δlon| > 180°) are never re-routed: they were
    already computed by searoute across the ±180° boundary and are correct.
    Re-sampling them in lon space produces bogus midpoints that span the
    globe, so we skip the land check entirely for these segments.
    """
    if not _LAND_MASK_AVAILABLE and _NE_TREE is None:
        return False
    # Guard 1 — antimeridian: searoute already routed the segment correctly;
    # geodesic sampling across ±180° produces bogus mid-globe land hits.
    if abs(lon2 - lon1) > 180:
        return False
    geod = Geodesic.WGS84
    try:
        line = geod.InverseLine(lat1, lon1, lat2, lon2)
        total_dist = line.s13
    except Exception:
        return False

    if total_dist < 1000:             # < 1 km — skip micro-segments (canal links)
        return False

    # Guard 2 — canal / passage endpoints: if either endpoint is on land AND
    # the segment is short (≤ 20 km), trust searoute's canal / strait routing.
    # Panama Canal, Suez Canal and all navigable straits are < 20 km per segment.
    # Segments longer than 20 km are checked even when an endpoint is on an island,
    # so that island-to-island routes don't cross intermediate landmasses silently.
    if total_dist <= 20_000 and (_is_land_hires(lat1, lon1) or _is_land_hires(lat2, lon2)):
        return False

    try:
        # When an endpoint is on an island/harbour, skip samples near that endpoint
        # to avoid false positives caused by the urban/coastal land mass surrounding
        # a port (e.g. Pointe-à-Pitre, Fort-de-France).  We skip the last 25 % of
        # samples so that real crossings further back along the segment are still
        # caught, while short "harbour approach" land hits are ignored.
        start_k = 3 if _is_land_hires(lat1, lon1) else 1
        end_k   = (n_samples * 3 // 4) if _is_land_hires(lat2, lon2) else n_samples

        # Require at least 2 *consecutive* land samples before reporting a crossing.
        # A single land sample on a ~75 km segment corresponds to a ~1–2 km island
        # or reef (e.g. Torres Strait cays, Maldive atolls) whose geodesic clip is a
        # false positive — searoute already routes around such obstacles correctly.
        # Real land masses (Basse-Terre ~30 km, Nias ~130 km, continents) produce
        # many consecutive samples and are still reliably detected.
        consecutive = 0
        for k in range(start_k, end_k):
            pos = line.Position(k / n_samples * total_dist)
            if _is_land_hires(pos["lat2"], pos["lon2"]):
                consecutive += 1
                if consecutive >= 2:
                    return True       # confirmed crossing: ≥ 2 consecutive land samples
            else:
                consecutive = 0      # reset on any ocean sample
    except Exception:
        pass
    return False


def _normalize_antimeridian(coords: list, prev_lon: float) -> list:
    """
    Walk through a list of [lon, lat] waypoints and adjust each longitude so
    that the sequence is continuous — no ±180° jumps.  This mirrors the same
    correction applied inside `_densify_coords` and is required whenever new
    waypoints are inserted by `searoute` or by a perpendicular detour, because
    those paths do not go through the densifier.

    `prev_lon` is the longitude of the last already-accepted waypoint (the
    point that precedes the first element of `coords`).
    Returns a NEW list with corrected longitudes (input is not mutated).
    """
    result = []
    for pt in coords:
        lon, lat = pt[0], pt[1]
        if lon - prev_lon > 180:
            lon -= 360
        elif lon - prev_lon < -180:
            lon += 360
        result.append([lon, lat])
        prev_lon = lon
    return result


def _reroute_segment(a: list, b: list) -> list:
    """
    Try to replace a land-crossing direct segment [a→b] with a proper
    maritime sub-route from searoute.
    Returns a list of [lon, lat] points that follow 'a' (i.e. does NOT
    include 'a' itself, DOES include 'b').
    Falls back to [b] (direct hop) when searoute also fails.
    """
    try:
        sub = sr.searoute((a[0], a[1]), (b[0], b[1]))
        if sub and sub.get("geometry", {}).get("coordinates"):
            sub_coords = sub["geometry"]["coordinates"]
            # Only use searoute result if it contains at least one intermediate
            # waypoint (> 2 points).  When searoute returns just [A, B] it means
            # it has no better route — fall through to perpendicular-offset strategy.
            if len(sub_coords) > 2:
                # Normalize antimeridian continuity relative to point 'a'
                normalized = _normalize_antimeridian(sub_coords[1:], a[0])
                return normalized    # exclude 'a' — already in result
    except Exception:
        pass
    return [b]   # fallback: direct connection (still not ideal, but keeps route valid)


def _fix_land_crossing_segments(coords: list) -> list:
    """
    Walk through consecutive waypoint pairs.  For any segment that crosses
    deeply-inland land, replace it with a maritime sub-route obtained from
    searoute.  Non-crossing segments are kept as-is for performance.
    """
    if not _LAND_MASK_AVAILABLE or len(coords) < 2:
        return coords

    result = [coords[0]]

    for i in range(len(coords) - 1):
        a = result[-1]        # last accepted/inserted point
        b = coords[i + 1]

        if _segment_crosses_land(a[0], a[1], b[0], b[1]):
            replacement = _reroute_segment(a, b)
            result.extend(replacement)
        else:
            result.append(b)

    return result


def _find_land_crossing_detour(a: list, b: list) -> list:
    """
    Given a segment A→B known to cross land, return a replacement list of
    waypoints (excluding A, including B) that navigates around the land.

    Tries in order:
      1. searoute(A, B)  — precomputed maritime routing graph
      2. Perpendicular-offset midpoint at 50 / 100 / 150 / 200 km on each
         side, evaluated at fractions 0.5 / 0.33 / 0.67 along the segment.
         The first candidate whose two sub-segments are both land-free is used.

    Falls back to [B] (direct hop) if every strategy fails.
    """
    # Strategy 1: maritime routing library
    geod = Geodesic.WGS84
    try:
        sub = sr.searoute((a[0], a[1]), (b[0], b[1]))
        if sub and sub.get("geometry", {}).get("coordinates"):
            sub_coords = sub["geometry"]["coordinates"]
            # Only accept if searoute added at least one INTERMEDIATE waypoint
            # (> 2 points = start + intermediate(s) + end).  When it returns
            # just [A, B] it found no better route — fall through to Strategy 2.
            if len(sub_coords) > 2:
                # Sanity check: reject spurious circumnavigations.
                # searoute's graph can produce absurd round-trip detours (e.g.
                # Torres Strait region: a 70 km or even 400 km direct segment
                # routed via a 2 000 km Coral Sea loop).  The per-point check
                # (distance from each intermediate to both endpoints) can miss
                # cases where the segment is long enough that a circumnavigation
                # intermediate lies within 3× of one endpoint.
                #
                # Primary check — total route length vs direct distance:
                # if the sum of all sub-segment distances exceeds 3× the direct
                # A→B distance the result is a circumnavigation, not a reroute.
                direct_dist = geod.Inverse(a[1], a[0], b[1], b[0])["s12"]
                total_route = sum(
                    geod.Inverse(
                        sub_coords[k][1], sub_coords[k][0],
                        sub_coords[k + 1][1], sub_coords[k + 1][0]
                    )["s12"]
                    for k in range(len(sub_coords) - 1)
                )
                if total_route <= direct_dist * 3:
                    # Normalize antimeridian continuity relative to point 'a'
                    normalized = _normalize_antimeridian(sub_coords[1:], a[0])
                    return normalized
    except Exception:
        pass

    # Strategy 2: perpendicular-offset midpoint detour
    geod = Geodesic.WGS84
    try:
        inv = geod.Inverse(a[1], a[0], b[1], b[0])
        bearing = inv["azi1"]
        dist = inv["s12"]
        mid_line = geod.InverseLine(a[1], a[0], b[1], b[0])

        # Adaptive offset distances: use finer steps for short segments so the
        # detour stays close enough to navigate narrow island channels/passages
        # (e.g. the approach to Pointe-à-Pitre around Basse-Terre, ~32km).
        if dist < 100_000:
            detour_distances_km = [10, 20, 30, 50]
        else:
            detour_distances_km = [50, 100, 150, 200]

        for frac in [0.5, 0.33, 0.67]:
            mid_pos = mid_line.Position(frac * dist)
            mid_lat, mid_lon = mid_pos["lat2"], mid_pos["lon2"]

            for sign in [1, -1]:          # try both sides of the segment
                for detour_km in detour_distances_km:
                    p = geod.Direct(
                        mid_lat, mid_lon,
                        (bearing + sign * 90) % 360,
                        detour_km * 1000
                    )
                    wp = [p["lon2"], p["lat2"]]
                    # Normalize the detour waypoint longitude for continuity
                    wp = _normalize_antimeridian([wp], a[0])[0]
                    if _is_land_hires(wp[1], wp[0]):
                        continue          # detour point itself is on land
                    seg1_ok = not _segment_crosses_land(a[0], a[1], wp[0], wp[1])
                    seg2_ok = not _segment_crosses_land(wp[0], wp[1], b[0], b[1])
                    if seg1_ok and seg2_ok:
                        # Also normalize b relative to the detour waypoint
                        b_norm = _normalize_antimeridian([b], wp[0])[0]
                        return [wp, b_norm]    # clean two-leg detour found
    except Exception:
        pass

    return [b]   # last-resort direct hop


def avoid_land(coords: list, max_iterations: int = 8) -> list:
    """
    Iteratively scan every consecutive segment in the route and reroute
    any that cross land, until the route is fully clean or max_iterations
    is reached.

    This is the core "Avoid Land" function: it guarantees that the rendered
    polyline never crosses a landmass or island as long as a detour can be
    found within the search parameters of _find_land_crossing_detour.
    """
    if not _LAND_MASK_AVAILABLE and _NE_TREE is None:
        return coords

    for iteration in range(max_iterations):
        changed = False
        result = [coords[0]]

        for i in range(len(coords) - 1):
            a = result[-1]
            b = coords[i + 1]

            if _segment_crosses_land(a[0], a[1], b[0], b[1]):
                detour = _find_land_crossing_detour(a, b)
                result.extend(detour)
                changed = True
            else:
                result.append(b)

        coords = result
        if not changed:
            print(f"✅ avoid_land: clean after {iteration + 1} iteration(s), {len(coords)} waypoints")
            break
    else:
        print(f"⚠️  avoid_land: max_iterations={max_iterations} reached, {len(coords)} waypoints")

    return coords


def _snap_to_ocean(lat: float, lon: float, max_radius_deg: float = 1.5) -> Optional[list]:
    """
    Given a point classified as land, find and return the nearest ocean cell
    whose centre lies STRICTLY within max_radius_deg of (lat, lon), using
    the 1/4° grid spacing of global_land_mask.

    Returns [lon, lat] of the nearest qualifying ocean cell, or None.
    """
    if not _LAND_MASK_AVAILABLE:
        return [lon, lat]

    grid = 0.25   # 1/4° matches global_land_mask resolution
    steps = int(max_radius_deg / grid) + 1
    max_sq = max_radius_deg ** 2        # strict radius² cut-off
    best_pt: Optional[list] = None
    best_dist_sq = float("inf")

    for di in range(-steps, steps + 1):
        for dj in range(-steps, steps + 1):
            dist_sq = (di * grid) ** 2 + (dj * grid) ** 2
            if dist_sq > max_sq:
                continue   # outside the requested radius
            if dist_sq >= best_dist_sq:
                continue   # already have a closer candidate
            try:
                tlat = lat + di * grid
                tlon = lon + dj * grid
                if not _globe.is_land(tlat, tlon):
                    best_pt = [tlon, tlat]
                    best_dist_sq = dist_sq
            except Exception:
                pass

    return best_pt


def _snap_to_ocean_fine(lat: float, lon: float,
                         radius_deg: float = 0.15,
                         grid: float = 0.01) -> Optional[list]:
    """
    High-resolution version of _snap_to_ocean.

    Searches for the nearest ocean point within *radius_deg* of (lat, lon)
    using a fine *grid* step (default 0.01° ≈ 1 km).  Uses the combined
    high-resolution land check (_is_land_hires) — Natural Earth 1:10m +
    global_land_mask — so it correctly resolves small-island harbours and
    narrow channels that the coarse 1/4° grid misses.

    Returns [lon, lat] of the closest water cell, or None if none found.
    """
    geod = Geodesic.WGS84
    steps = int(radius_deg / grid) + 1
    max_sq = radius_deg ** 2

    best_pt: Optional[list] = None
    best_dist_m = float("inf")

    for di in range(-steps, steps + 1):
        for dj in range(-steps, steps + 1):
            dist_sq = (di * grid) ** 2 + (dj * grid) ** 2
            if dist_sq > max_sq:
                continue                      # outside search radius
            tlat = lat + di * grid
            tlon = lon + dj * grid
            if _is_land_hires(tlat, tlon):
                continue                      # still on land
            dist_m = geod.Inverse(lat, lon, tlat, tlon)["s12"]
            if dist_m < best_dist_m:
                best_dist_m = dist_m
                best_pt = [tlon, tlat]

    return best_pt


def _densify_coords(coords: list, max_km: float = 75.0) -> list:
    """
    Insert geodesic intermediate waypoints so that no consecutive pair is
    further apart than max_km.  This prevents the map renderer (MapLibre)
    from drawing a straight line that visually crosses a landmass between
    two ocean waypoints.

    Antimeridian wrapping is preserved.
    """
    geod = Geodesic.WGS84
    max_m = max_km * 1000.0
    result = [coords[0]]

    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        try:
            line = geod.InverseLine(a[1], a[0], b[1], b[0])
            dist = line.s13
            if dist > max_m:
                n = int(dist / max_m) + 1
                for j in range(1, n):
                    pos = line.Position(j / n * dist)
                    lon = pos["lon2"]
                    # keep antimeridian continuity
                    prev_lon = result[-1][0]
                    if lon - prev_lon > 180:
                        lon -= 360
                    elif lon - prev_lon < -180:
                        lon += 360
                    result.append([lon, pos["lat2"]])
        except Exception:
            pass
        result.append(b)

    return result


def _sanitize_route_coords(coords: list) -> list:
    """
    Ensure every intermediate waypoint lies in the ocean:

    • Ocean cells                           → kept exactly as-is.
    • Land cells with ocean within 0.5°     → snapped to that ocean cell.
      (coastal nodes slightly inside a land cell at 1/4° resolution)
    • Land cells with ocean only within 1.5° → kept as-is.
      (narrow maritime passages: Suez Canal, Panama Canal, straits —
       the nearest ocean is the distant sea, NOT a nearby lake; the node
       is still a valid routing guide through the passage)
    • Land cells with no ocean within 1.5°  → dropped (genuinely inland).

    First and last points (departure / destination ports) are always kept
    unchanged — ships dock at coasts.
    """
    if not _LAND_MASK_AVAILABLE and _NE_TREE is None:
        return coords

    cleaned = [coords[0]]   # always keep departure

    for i in range(1, len(coords) - 1):
        lon, lat = coords[i]
        on_land = _is_land_hires(lat, lon)

        if not on_land:
            cleaned.append(coords[i])                    # ocean → keep
        else:
            # tight snap ≤ 0.3° (one grid cell) — catches coastal clips only.
            # Canal/strait nodes are typically 0.35°+ from open water so they
            # don't qualify here and fall through to the passage-keep rule.
            snapped = _snap_to_ocean(lat, lon, max_radius_deg=0.3)
            if snapped:
                cleaned.append(snapped)                  # coastal clip → snap
            elif _has_nearby_ocean(lat, lon, radius_deg=1.5):
                cleaned.append(coords[i])               # canal/passage → keep as-is
            # else: genuinely inland → drop silently

    cleaned.append(coords[-1])  # always keep destination
    return cleaned


def _route_cache_key(start, end) -> tuple:
    """
    Bidirectional cache key: always places the lexicographically smaller point
    first so that A→B and B→A map to the same key.
    Coordinates are rounded to 4 decimal places (~11 m precision) so that
    floating-point noise doesn't create spurious cache misses.
    """
    p1 = (round(start[0], 4), round(start[1], 4))
    p2 = (round(end[0], 4), round(end[1], 4))
    return (min(p1, p2), max(p1, p2))


# In-memory route cache  {cache_key: (route_dict, canonical_start_tuple)}
# The canonical_start is whichever endpoint was used as start when the route
# was first computed; the cache consumer flips the coordinate list when the
# request goes in the opposite direction.
_route_cache: dict = {}


def searoute_with_exact_end(start, end):
    """
    Calcule une route maritime entre deux points et ajoute un segment géodésique
    jusqu'à la destination exacte si searoute s'arrête trop tôt.
    Gère correctement le passage de l'antiméridien (180°/-180°).

    Bidirectional cache: if the reverse segment B→A was already computed, the
    cached coordinate list is reversed and returned immediately — ensuring that
    the aller and retour legs between the same two waypoints render as a single
    identical line on the map.
    """
    import copy

    cache_key = _route_cache_key(start, end)
    canonical_start = (round(start[0], 4), round(start[1], 4))

    if cache_key in _route_cache:
        cached_route, cached_canonical_start = _route_cache[cache_key]
        route = copy.deepcopy(cached_route)
        # If this request goes in the opposite direction, reverse the coords
        if canonical_start != cached_canonical_start:
            route["geometry"]["coordinates"].reverse()
        return route

    try:
        route = sr.searoute(start, end)
    except Exception as e:
        print(f"⚠️ Erreur searoute: {e}")
        return None

    if not route or "geometry" not in route:
        return None

    coords = route["geometry"]["coordinates"]

    geod = Geodesic.WGS84

    # ── Exact start: prepend the origin if searoute snapped to a different node ──
    first_point = coords[0]
    start_dist = geod.Inverse(start[1], start[0], first_point[1], first_point[0])["s12"]
    if start_dist > 1000:
        coords.insert(0, [start[0], start[1]])

    last_point = coords[-1]
    dist = geod.Inverse(last_point[1], last_point[0], end[1], end[0])["s12"]

    if dist > 1000:
        # Append the exact endpoint as a single segment — avoid_land will insert
        # detour waypoints as needed.  Pre-interpolating with many 5 km sub-points
        # previously caused problems: each tiny sub-segment crossing a large island
        # (e.g. Basse-Terre / Guadeloupe) could not be individually rerouted, leading
        # to an oscillating zigzag that never converged.
        line = geod.InverseLine(last_point[1], last_point[0], end[1], end[0])
        pos = line.Position(line.s13)
        lon = pos["lon2"]
        lat = pos["lat2"]
        # Antimeridian normalisation relative to the preceding point
        prev_lon = coords[-1][0]
        if lon - prev_lon > 180:
            lon -= 360
        elif lon - prev_lon < -180:
            lon += 360
        coords.append([lon, lat])

    # ── Avoid-Land Pipeline ─────────────────────────────────────────────────

    # Step 1 — initial avoid-land pass: iteratively reroute every segment
    #           that crosses any landmass or island (major coasts, peninsulas,
    #           small islands like Nias / Mentawai).
    coords = avoid_land(coords)

    # Step 2 — densify: insert intermediate waypoints so no segment exceeds
    #           75 km, preventing the renderer from drawing a chord that
    #           visually crosses land between two distant ocean points.
    coords = _densify_coords(coords, max_km=75)

    # Step 3 — per-point cleanup: snap coastal clips to the nearest ocean
    #           cell, preserve canal / strait passage nodes, drop inland stray
    #           points introduced by earlier steps.
    coords = _sanitize_route_coords(coords)

    # Step 4 — second avoid-land pass: densification in Step 2 can place new
    #           geodesic midpoints on small islands (e.g. Nias).  This final
    #           iterative scan catches and reroutes any remaining crossings.
    coords = avoid_land(coords, max_iterations=5)

    route["geometry"]["coordinates"] = coords

    # Store in bidirectional cache so the reverse leg reuses this result
    _route_cache[cache_key] = (copy.deepcopy(route), canonical_start)

    return route


app = FastAPI(
    title="NAVIGUIDE API",
    description="API pour calculer un itinéraire maritime — Berry-Mappemonde Expedition.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PositionRequest(BaseModel):
    latitude: float
    longitude: float


@app.get("/")
def read_root():
    return {
        "message": "NAVIGUIDE API is running",
        "version": "1.0.0",
        "copernicus_configured": bool(COPERNICUS_USERNAME and COPERNICUS_PASSWORD)
    }


@app.get("/route")
def get_route(
    start_lat: float = Query(...),
    start_lon: float = Query(...),
    end_lat: float = Query(...),
    end_lon: float = Query(...),
    check_wind: bool = Query(False),
    sample_rate: int = Query(100)
):
    """Calcule une route maritime et renvoie le GeoJSON."""
    start = (start_lon, start_lat)
    end = (end_lon, end_lat)

    try:
        route = searoute_with_exact_end(start, end)
        if route is None:
            raise HTTPException(status_code=404, detail="Route non trouvée")

        if check_wind and COPERNICUS_USERNAME and COPERNICUS_PASSWORD:
            route = add_wind_properties_to_route(
                route,
                username=COPERNICUS_USERNAME,
                password=COPERNICUS_PASSWORD,
                sample_rate=sample_rate
            )
        elif check_wind and not (COPERNICUS_USERNAME and COPERNICUS_PASSWORD):
            # Return route without wind overlay if no credentials
            return {
                "type": "FeatureCollection",
                "features": [route]
            }

        return route

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sim_wind(lat: float, lon: float) -> dict:
    """Simulation fallback — field names match frontend expectations exactly."""
    import random, datetime
    rng = random.Random(int(abs(lat * 100) + abs(lon * 100)))
    speed_ms    = rng.uniform(3, 18)
    speed_kmh   = round(speed_ms * 3.6, 1)
    speed_knots = round(speed_ms * 1.944, 1)
    direction   = round(rng.uniform(0, 360), 1)
    u = round(-speed_ms * math.sin(math.radians(direction)), 3)
    v = round(-speed_ms * math.cos(math.radians(direction)), 3)
    return {
        "latitude": lat, "longitude": lon,
        "u_component": u, "v_component": v,
        "wind_speed": round(speed_ms, 2),
        "wind_speed_kmh": speed_kmh,
        "wind_speed_knots": speed_knots,
        "wind_direction": direction,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "simulation": True, "source": "estimated (Copernicus unavailable)",
    }

def _sim_wave(lat: float, lon: float) -> dict:
    """Simulation fallback — field names match frontend expectations exactly."""
    import random, datetime
    rng = random.Random(int(abs(lat * 137) + abs(lon * 73)))
    height  = round(rng.uniform(0.3, 4.5), 2)
    period  = round(rng.uniform(4, 14), 1)
    direction = round(rng.uniform(0, 360), 1)
    return {
        "latitude": lat, "longitude": lon,
        "significant_wave_height_m": height,   # exact field name frontend expects
        "mean_wave_period": period,
        "mean_wave_direction": direction,       # exact field name frontend expects
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "simulation": True, "source": "estimated (Copernicus unavailable)",
    }

def _sim_current(lat: float, lon: float) -> dict:
    """Simulation fallback — field names match frontend expectations exactly."""
    import random, datetime
    rng = random.Random(int(abs(lat * 211) + abs(lon * 157)))
    speed_ms    = rng.uniform(0.05, 1.2)
    speed_knots = round(speed_ms * 1.944, 2)
    speed_kmh   = round(speed_ms * 3.6, 2)
    direction   = round(rng.uniform(0, 360), 1)
    u = round(speed_ms * math.sin(math.radians(direction)), 4)
    v = round(speed_ms * math.cos(math.radians(direction)), 4)
    return {
        "latitude": lat, "longitude": lon,
        "u_component": u, "v_component": v,
        "speed_ms": round(speed_ms, 3),
        "speed_knots": speed_knots,             # exact field name frontend expects
        "speed_kmh": speed_kmh,                 # exact field name frontend expects
        "direction_deg": direction,             # exact field name frontend expects
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "simulation": True, "source": "estimated (Copernicus unavailable)",
    }


@app.post("/wind")
def get_wind(request: PositionRequest):
    """Récupère les données de vent via Copernicus Marine, avec fallback simulation."""
    try:
        if COPERNICUS_USERNAME and COPERNICUS_PASSWORD:
            wind_data = get_wind_data_at_position(
                latitude=request.latitude,
                longitude=request.longitude,
                username=COPERNICUS_USERNAME,
                password=COPERNICUS_PASSWORD
            )
            if wind_data is not None:
                return wind_data
    except Exception:
        pass
    return _sim_wind(request.latitude, request.longitude)


@app.post("/wave")
def get_wave(request: PositionRequest):
    """Récupère les données de vague via Copernicus Marine, avec fallback simulation."""
    try:
        if COPERNICUS_USERNAME and COPERNICUS_PASSWORD:
            wave_data = get_wave_data_at_position(
                latitude=request.latitude,
                longitude=request.longitude,
                username=COPERNICUS_USERNAME,
                password=COPERNICUS_PASSWORD
            )
            if wave_data is not None:
                return wave_data
    except Exception:
        pass
    return _sim_wave(request.latitude, request.longitude)


@app.post("/current")
def get_current(request: PositionRequest):
    """Récupère les données de courant via Copernicus Marine, avec fallback simulation."""
    try:
        if COPERNICUS_USERNAME and COPERNICUS_PASSWORD:
            current_data = get_current_data_at_position(
                latitude=request.latitude,
                longitude=request.longitude,
                username=COPERNICUS_USERNAME,
                password=COPERNICUS_PASSWORD
            )
            if current_data is not None:
                return current_data
    except Exception:
        pass
    return _sim_current(request.latitude, request.longitude)


# ── Maritime data proxy routes (CORS bypass) ─────────────────────────────────

# In-memory cache for WPI ports (24 h TTL — data rarely changes)
_wpi_cache: dict = {"data": None, "ts": 0.0}
_WPI_CACHE_TTL = 86_400  # seconds

# DMS coordinate pattern: e.g. "30°20'00\"N" or "48°17'00\"E"
_DMS_RE = re.compile(
    r"""(\d+)\s*[°d]\s*(\d+)\s*[''′]\s*(\d+(?:\.\d+)?)\s*[""″]?\s*([NSEW]?)""",
    re.IGNORECASE,
)

def _parse_coord(value: Optional[Union[str, float, int]]) -> Optional[float]:
    """
    Parse a coordinate value that may be decimal or DMS format.
    Returns float decimal degrees, or None if unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # Try plain float first
    try:
        return float(s)
    except ValueError:
        pass
    # Try DMS
    m = _DMS_RE.search(s)
    if m:
        deg, mins, secs, hemi = m.groups()
        decimal = float(deg) + float(mins) / 60.0 + float(secs) / 3600.0
        if hemi.upper() in ("S", "W"):
            decimal = -decimal
        return decimal
    return None


@app.get("/proxy/zee", summary="ZEE boundaries proxy (VLIZ WFS)")
async def proxy_zee(
    bbox: Optional[str] = Query(
        None,
        description="Viewport bounding box as minlon,minlat,maxlon,maxlat (CRS:84)",
    ),
    maxFeatures: int = Query(50, ge=1, le=500, description="Max EEZ polygons to return"),
):
    """
    Proxy pour l'API WFS VLIZ Marine Regions — ZEE (Zones Économiques Exclusives).
    Contourne les restrictions CORS du serveur VLIZ.
    """
    params: dict = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": "eez",
        "outputFormat": "application/json",
        "maxFeatures": maxFeatures,
    }
    if bbox:
        params["bbox"] = bbox
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://geo.vliz.be/geoserver/MarineRegions/wfs",
                params=params,
            )
            resp.raise_for_status()
            return JSONResponse(content=resp.json())
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"ZEE upstream HTTP error: {exc.response.status_code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ZEE upstream error: {exc}")


@app.get("/proxy/ports", summary="WPI world ports as GeoJSON (NGA/MSI)")
async def proxy_ports():
    """
    Retourne les ports mondiaux du World Port Index (NGA/MSI) en GeoJSON.
    Résultat mis en cache 24 h côté serveur.
    """
    # Serve from cache if still fresh
    if _wpi_cache["data"] and (time.time() - _wpi_cache["ts"] < _WPI_CACHE_TTL):
        return JSONResponse(content=_wpi_cache["data"])

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                "https://msi.nga.mil/api/publications/world-port-index",
                params={"output": "json"},
            )
            resp.raise_for_status()
            raw = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"WPI upstream HTTP error: {exc.response.status_code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WPI upstream error: {exc}")

    # Normalise: the API may return a list or {"ports": [...]}
    ports = raw if isinstance(raw, list) else raw.get("ports", [])

    features = []
    for p in ports:
        lat = _parse_coord(p.get("latitude") or p.get("lat"))
        lon = _parse_coord(p.get("longitude") or p.get("lon"))
        if lat is None or lon is None:
            continue
        if lat == 0.0 and lon == 0.0:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name":    p.get("portName")    or p.get("name",    ""),
                "country": p.get("countryName") or p.get("country", ""),
                "region":  p.get("regionName")  or p.get("region",  ""),
                "wpi_num": p.get("portNumber")  or p.get("indexNo", ""),
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    _wpi_cache["data"] = geojson
    _wpi_cache["ts"]   = time.time()
    return JSONResponse(content=geojson)


# NOTE: SHOM WFS /proxy/balisage removed — endpoint requires authentication (401).
# Balisage is now served client-side via OpenSeaMap raster tiles (no proxy needed).


# ── Simulation Mode — Geometry Helpers ───────────────────────────────────────

_DEFAULT_CATAMARAN_SPEED_KTS = 7.5  # conservative offshore sailing speed


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from (lat1, lon1) to (lat2, lon2) in degrees [0, 360)."""
    dlon = math.radians(lon2 - lon1)
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _snap_to_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> tuple:
    """
    Project point P(px=lon, py=lat) onto segment A-B in degree space.
    Returns (qx, qy, t) where t ∈ [0, 1] is the normalised position along AB.
    """
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 < 1e-14:
        return ax, ay, 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    return ax + t * dx, ay + t * dy, t


def _snap_catamaran_to_route(
    cat_lat: float,
    cat_lon: float,
    route_coords: List[List[float]],
) -> dict:
    """
    Snap the catamaran position to the nearest point on the route polyline.
    route_coords — [[lon, lat], …] GeoJSON order.

    Returns:
        snapped_lon, snapped_lat, seg_idx, nm_covered
    """
    best_dist      = float("inf")
    best_lon       = route_coords[0][0]
    best_lat       = route_coords[0][1]
    best_seg       = 0
    best_nm_covered = 0.0
    cumulative_nm   = 0.0

    for i in range(len(route_coords) - 1):
        a_lon, a_lat = route_coords[i][0], route_coords[i][1]
        b_lon, b_lat = route_coords[i + 1][0], route_coords[i + 1][1]

        qlon, qlat, _ = _snap_to_segment(cat_lon, cat_lat, a_lon, a_lat, b_lon, b_lat)
        dist = _haversine_nm(cat_lat, cat_lon, qlat, qlon)

        if dist < best_dist:
            best_dist = dist
            best_lon, best_lat = qlon, qlat
            best_seg = i
            best_nm_covered = cumulative_nm + _haversine_nm(a_lat, a_lon, qlat, qlon)

        cumulative_nm += _haversine_nm(a_lat, a_lon, b_lat, b_lon)

    return {
        "snapped_lon": best_lon,
        "snapped_lat": best_lat,
        "seg_idx":     best_seg,
        "nm_covered":  round(best_nm_covered, 1),
    }


def _find_active_leg(
    cat_nm_covered: float,
    route_coords:   List[List[float]],
    stops:          List[dict],
) -> dict:
    """
    Determine the active stop-to-stop leg and remaining distance to next stop.

    stops — ordered list of {"name": str, "lon": float, "lat": float}
    Returns from_stop_index, from_stop, to_stop, nm_remaining_to_stop.
    """
    # Compute each stop's nm_covered position on the route
    stop_positions = []
    for stop in stops:
        snap = _snap_catamaran_to_route(stop["lat"], stop["lon"], route_coords)
        stop_positions.append({
            "name":       stop["name"],
            "nm_covered": snap["nm_covered"],
        })

    # Sort by route distance (should already be ordered, but defensive)
    stop_positions.sort(key=lambda x: x["nm_covered"])

    # Find the active bracket: largest stop before cat, first stop after cat
    from_idx = 0
    for i, sp in enumerate(stop_positions):
        if sp["nm_covered"] <= cat_nm_covered:
            from_idx = i

    to_idx = min(from_idx + 1, len(stop_positions) - 1)

    from_sp = stop_positions[from_idx]
    to_sp   = stop_positions[to_idx]
    nm_remaining = max(0.0, to_sp["nm_covered"] - cat_nm_covered)

    return {
        "from_stop_index":     from_idx,
        "from_stop":           from_sp["name"],
        "to_stop":             to_sp["name"],
        "nm_remaining_to_stop": round(nm_remaining, 1),
    }


# ── Simulation Mode — Pydantic Models ────────────────────────────────────────

class SimulationStop(BaseModel):
    name: str
    lon:  float
    lat:  float


class SimulationPositionRequest(BaseModel):
    lat:          float
    lon:          float
    route_coords: List[List[float]]   # [[lon, lat], …]
    stops:        List[SimulationStop]
    speed_kts:    Optional[float] = _DEFAULT_CATAMARAN_SPEED_KTS


class AgentRequest(BaseModel):
    from_stop:    str
    to_stop:      str
    lat:          float
    lon:          float
    nm_remaining: float
    language:     str = "fr"


# ── Simulation Mode — /simulation/position ───────────────────────────────────

@app.post("/simulation/position", summary="Snap catamaran to route + compute leg metrics")
def simulation_position(req: SimulationPositionRequest):
    """
    Snap the catamaran marker to the nearest point on the route polyline and
    compute progression metrics for the active leg.

    Returns LegContext: snappedPosition, fromStop, toStop, nmCovered,
    nmRemainingToStop, etaHours, bearing.
    """
    if len(req.route_coords) < 2:
        raise HTTPException(status_code=400, detail="route_coords must contain at least 2 points")
    if len(req.stops) < 2:
        raise HTTPException(status_code=400, detail="stops must contain at least 2 stops")

    # Snap catamaran to route
    snap = _snap_catamaran_to_route(req.lat, req.lon, req.route_coords)

    # Find active leg
    stops_list = [{"name": s.name, "lon": s.lon, "lat": s.lat} for s in req.stops]
    leg        = _find_active_leg(snap["nm_covered"], req.route_coords, stops_list)

    # Bearing at the active segment
    seg_idx = min(snap["seg_idx"], len(req.route_coords) - 2)
    a_lon, a_lat = req.route_coords[seg_idx][0], req.route_coords[seg_idx][1]
    b_lon, b_lat = req.route_coords[seg_idx + 1][0], req.route_coords[seg_idx + 1][1]
    bearing = round(_bearing_deg(a_lat, a_lon, b_lat, b_lon), 1)

    # ETA to next stop
    speed    = req.speed_kts if req.speed_kts and req.speed_kts > 0 else _DEFAULT_CATAMARAN_SPEED_KTS
    eta_hours = round(leg["nm_remaining_to_stop"] / speed, 1)

    return {
        "fromStopIndex":     leg["from_stop_index"],
        "fromStop":          leg["from_stop"],
        "toStop":            leg["to_stop"],
        "nmCovered":         snap["nm_covered"],
        "nmRemainingToStop": leg["nm_remaining_to_stop"],
        "etaHours":          eta_hours,
        "bearing":           bearing,
        "snappedPosition":   [snap["snapped_lon"], snap["snapped_lat"]],
    }


# ── Simulation Mode — Agent Endpoints ────────────────────────────────────────
# All 4 agents stream token-by-token via Anthropic SSE for progressive display
# in the frontend AgentPanel. Each endpoint:
#   1. Runs the agent's data-fetch pipeline synchronously (in threadpool)
#   2. Builds the LLM prompt from the fetched context
#   3. Streams Anthropic tokens as SSE  data: {"token": "..."}  events
#   4. Terminates with  data: [DONE]


@app.post("/agents/custom", summary="Agent Custom — Port & Customs Intelligence (SSE)")
async def agent_custom(req: AgentRequest):
    """
    Invoke the Custom LangGraph agent for port entry intelligence.
    Streams token-by-token as SSE data: {"token": "..."} events.
    """
    async def generator():
        from agents.custom_agent import get_streaming_prompt
        from agents.deploy_ai import stream_llm

        # Build prompt (sync — no I/O, runs in current thread)
        prompt = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_streaming_prompt(
                from_stop=req.from_stop,
                to_stop=req.to_stop,
                lat=req.lat,
                lon=req.lon,
                nm_remaining=req.nm_remaining,
                language=req.language,
            ),
        )

        has_content = False
        try:
            async for token in stream_llm(prompt):
                if token:
                    has_content = True
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception:
            pass

        if not has_content:
            fallback = (
                f"## {req.to_stop} — Port Intelligence\n\n"
                f"⚠️ **LLM service temporarily unavailable.**\n\n"
                f"**Recommended resources:**\n"
                f"- 🌐 [Noonsite](https://www.noonsite.com) — search for {req.to_stop}\n"
                f"- 📖 Local pilot charts & sailing almanac\n"
                f"- 📡 VHF Ch 16 → harbour authority on arrival\n\n"
                f"Distance remaining: **{req.nm_remaining:.0f} nm**."
            )
            yield f"data: {json.dumps({'token': fallback})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/agents/guard", summary="Agent Guard — Maritime Security (SSE)")
async def agent_guard(req: AgentRequest):
    """
    Invoke the Guard LangGraph agent for maritime security intelligence.
    Streams token-by-token as SSE data: {"token": "..."} events.
    Includes live IMB piracy-data fetch before streaming LLM output.
    """
    async def generator():
        from agents.guard_agent import get_streaming_prompt
        from agents.deploy_ai import stream_llm

        # Build prompt — includes live IMB piracy-data fetch (sync, in threadpool)
        prompt = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_streaming_prompt(
                from_stop=req.from_stop,
                to_stop=req.to_stop,
                lat=req.lat,
                lon=req.lon,
                nm_remaining=req.nm_remaining,
                language=req.language,
            ),
        )

        has_content = False
        try:
            async for token in stream_llm(prompt):
                if token:
                    has_content = True
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception:
            pass

        if not has_content:
            fallback = (
                f"## {req.to_stop} — Maritime Security\n\n"
                f"⚠️ **LLM service temporarily unavailable.**\n\n"
                f"**Recommended resources:**\n"
                f"- 🌐 [IMB Piracy Reporting Centre](https://www.icc-ccs.org/piracy-reporting-centre)\n"
                f"- 📡 VHF Ch 16 on arrival\n\n"
                f"Distance remaining: **{req.nm_remaining:.0f} nm**."
            )
            yield f"data: {json.dumps({'token': fallback})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/agents/meteo", summary="Agent Meteo — Weather & Routing Windows (SSE)")
async def agent_meteo(req: AgentRequest):
    """
    Invoke the Meteo LangGraph agent for weather and routing windows.
    Streams token-by-token as SSE data: {"token": "..."} events.
    Includes StormGlass weather fetch before streaming LLM output.
    """
    async def generator():
        from agents.meteo_agent import get_streaming_prompt
        from agents.deploy_ai import stream_llm

        # Build prompt — includes StormGlass weather fetch (sync, in threadpool)
        prompt = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_streaming_prompt(
                from_stop=req.from_stop,
                to_stop=req.to_stop,
                lat=req.lat,
                lon=req.lon,
                nm_remaining=req.nm_remaining,
                language=req.language,
            ),
        )

        has_content = False
        try:
            async for token in stream_llm(prompt):
                if token:
                    has_content = True
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception:
            pass

        if not has_content:
            fallback = (
                f"## {req.to_stop} — Weather Briefing\n\n"
                f"⚠️ **LLM service temporarily unavailable.**\n\n"
                f"**Recommended resources:**\n"
                f"- 🌐 [Windy](https://www.windy.com) — real-time weather\n"
                f"- 📡 NavTex / SSB weatherfax\n\n"
                f"Distance remaining: **{req.nm_remaining:.0f} nm**."
            )
            yield f"data: {json.dumps({'token': fallback})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/agents/pirate", summary="Agent Pirate — Community Intelligence (SSE)")
async def agent_pirate(req: AgentRequest):
    """
    Invoke the Pirate LangGraph agent for cruiser community intelligence.
    Streams token-by-token as SSE data: {"token": "..."} events.
    Includes Noonsite RSS fetch before streaming LLM output.
    """
    async def generator():
        from agents.pirate_agent import get_streaming_prompt
        from agents.deploy_ai import stream_llm

        # Build prompt — includes Noonsite RSS fetch (sync, in threadpool)
        prompt = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_streaming_prompt(
                from_stop=req.from_stop,
                to_stop=req.to_stop,
                lat=req.lat,
                lon=req.lon,
                nm_remaining=req.nm_remaining,
                language=req.language,
            ),
        )

        has_content = False
        try:
            async for token in stream_llm(prompt):
                if token:
                    has_content = True
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception:
            pass

        if not has_content:
            fallback = (
                f"## {req.to_stop} — Community Intelligence\n\n"
                f"⚠️ **LLM service temporarily unavailable.**\n\n"
                f"**Recommended resources:**\n"
                f"- 🌐 [Noonsite Forums](https://www.noonsite.com)\n"
                f"- 🌐 [Cruisers Forum](https://www.cruisersforum.com)\n\n"
                f"Distance remaining: **{req.nm_remaining:.0f} nm**."
            )
            yield f"data: {json.dumps({'token': fallback})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 3007))
    uvicorn.run(app, host="0.0.0.0", port=port)
