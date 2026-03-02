/**
 * useLegContext — Calcul géométrique du tronçon actif
 *
 * Snap-to-route : projection haversine manuelle sur chaque segment de la polyligne.
 * Aucun appel API, aucune dépendance externe (pas de @turf).
 *
 * Tous les points ITINERARY_POINTS sont utilisés (escales obligatoires ET points
 * intermédiaires) pour un suivi segment par segment de la route complète :
 *   escale → intermédiaire → intermédiaire → escale suivante
 *
 * Returns LegContext :
 *   fromStopIndex     — index du point d'origine (escale ou intermédiaire)
 *   fromStop          — nom du point d'origine
 *   toStop            — nom du prochain point (escale ou intermédiaire)
 *   toStopIndex       — index du prochain point
 *   nmCovered         — miles nautiques parcourus depuis le départ (total route)
 *   nmRemainingToStop — miles nautiques restants jusqu'au prochain point
 *   etaHours          — ETA estimée (vitesse constante par défaut)
 *   bearing           — cap actuel en degrés (0–360)
 *   snappedPosition   — [lon, lat] — position projetée sur la route
 */

import { useMemo } from "react";

// Vitesse par défaut du catamaran (nœuds)
const DEFAULT_SPEED_KNOTS = 7;

// ── Haversine ────────────────────────────────────────────────────────────────

const R_NM = 3440.065; // rayon terrestre en milles nautiques

function toRad(deg) { return (deg * Math.PI) / 180; }

function haversineNm(lat1, lon1, lat2, lon2) {
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R_NM * Math.asin(Math.sqrt(a));
}

/**
 * Cap initial (bearing) entre deux points géodésiques, en degrés (0–360).
 */
function initialBearing(lat1, lon1, lat2, lon2) {
  const φ1 = toRad(lat1);
  const φ2 = toRad(lat2);
  const Δλ = toRad(lon2 - lon1);
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Projette le point P (lat, lon) sur le segment AB.
 * Retourne { point: [lon, lat], t: 0..1, distNm } où t=0→A, t=1→B.
 *
 * Approximation plane valide pour segments courts (< 500 nm).
 */
function projectOnSegment(pLat, pLon, aLat, aLon, bLat, bLon) {
  const ax = aLon; const ay = aLat;
  const bx = bLon; const by = bLat;
  const px = pLon; const py = pLat;

  const dx = bx - ax; const dy = by - ay;
  const lenSq = dx * dx + dy * dy;

  let t = 0;
  if (lenSq > 0) {
    t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
  }

  const qLon = ax + t * dx;
  const qLat = ay + t * dy;
  const distNm = haversineNm(pLat, pLon, qLat, qLon);
  return { point: [qLon, qLat], t, distNm };
}

// ── Hook ─────────────────────────────────────────────────────────────────────

/**
 * @param {number|null} catamaranLat   — latitude du catamaran (null si non activé)
 * @param {number|null} catamaranLon   — longitude du catamaran
 * @param {Array}       routeSegments  — segments calculés par App.jsx [ {coords: [[lon,lat],...], nonMaritime?} ]
 * @param {Array}       itineraryPoints — ITINERARY_POINTS (escales + points intermédiaires)
 * @param {number}      speedKnots     — vitesse en nœuds (optionnel)
 */
export function useLegContext(
  catamaranLat,
  catamaranLon,
  routeSegments,
  itineraryPoints,
  speedKnots = DEFAULT_SPEED_KNOTS,
) {
  return useMemo(() => {
    if (catamaranLat == null || catamaranLon == null) return null;
    if (!routeSegments || routeSegments.length === 0) return null;

    // ── 1. Flatten route segments into a single polyline [[lon, lat], ...] ──
    const polyline = [];
    for (const seg of routeSegments) {
      if (!seg.coords || seg.coords.length < 2) continue;
      if (polyline.length === 0) {
        polyline.push(...seg.coords);
      } else {
        // Skip first point of subsequent segments (duplicate junction)
        polyline.push(...seg.coords.slice(1));
      }
    }
    if (polyline.length < 2) return null;

    // ── 2. Snap catamaran to nearest point on polyline ───────────────────────
    let bestDist = Infinity;
    let bestSnap = null;
    let bestSegIdx = 0;
    let bestT = 0;

    for (let i = 0; i < polyline.length - 1; i++) {
      const [aLon, aLat] = polyline[i];
      const [bLon, bLat] = polyline[i + 1];
      const res = projectOnSegment(catamaranLat, catamaranLon, aLat, aLon, bLat, bLon);
      if (res.distNm < bestDist) {
        bestDist = res.distNm;
        bestSnap = res.point;   // [lon, lat]
        bestSegIdx = i;
        bestT = res.t;
      }
    }

    if (!bestSnap) return null;
    const [snapLon, snapLat] = bestSnap;

    // ── 3. Compute cumulative nm from route start to snapped position ────────
    let nmCoveredTotal = 0;
    for (let i = 0; i < bestSegIdx; i++) {
      const [aLon, aLat] = polyline[i];
      const [bLon, bLat] = polyline[i + 1];
      nmCoveredTotal += haversineNm(aLat, aLon, bLat, bLon);
    }
    // Add partial segment
    const [aLon, aLat] = polyline[bestSegIdx];
    const [bLon, bLat] = polyline[bestSegIdx + 1];
    nmCoveredTotal += bestT * haversineNm(aLat, aLon, bLat, bLon);

    // ── 4. Identify active leg (fromStop → toStop) ──────────────────────────
    // Inclure TOUS les points (escales obligatoires ET points intermédiaires)
    // pour que les agents traitent la route complète :
    //   escale → intermédiaire → intermédiaire → escale suivante
    const stops = (itineraryPoints || []);

    // For each point, find its nearest polyline index
    function nearestPolylineIdx(stopLat, stopLon) {
      let best = 0; let bestD = Infinity;
      for (let i = 0; i < polyline.length; i++) {
        const [pLon, pLat] = polyline[i];
        const d = haversineNm(stopLat, stopLon, pLat, pLon);
        if (d < bestD) { bestD = d; best = i; }
      }
      return best;
    }

    // Map stops to their polyline index
    const stopIndices = stops.map((s) => ({
      stop: s,
      polyIdx: nearestPolylineIdx(s.lat, s.lon),
    }));

    // Sort by polyline index to get travel order
    stopIndices.sort((a, b) => a.polyIdx - b.polyIdx);

    // Find the stop immediately after the snapped position
    let fromStop = stopIndices[0]?.stop;
    let toStop   = stopIndices[stopIndices.length - 1]?.stop;
    let fromIdx  = 0;
    let toIdx    = stopIndices.length - 1;

    for (let i = 0; i < stopIndices.length; i++) {
      if (stopIndices[i].polyIdx > bestSegIdx) {
        toStop  = stopIndices[i].stop;
        toIdx   = i;
        fromStop = i > 0 ? stopIndices[i - 1].stop : stopIndices[0].stop;
        fromIdx  = i > 0 ? i - 1 : 0;
        break;
      }
    }

    // ── 5. NM remaining from snap to toStop ─────────────────────────────────
    const toStopPolyIdx = stopIndices[toIdx]?.polyIdx ?? polyline.length - 1;
    let nmRemainingToStop = 0;
    if (toStopPolyIdx > bestSegIdx) {
      // Partial current segment
      nmRemainingToStop += (1 - bestT) * haversineNm(aLat, aLon, bLat, bLon);
      // Full segments until toStop
      for (let i = bestSegIdx + 1; i < toStopPolyIdx; i++) {
        const [cLon, cLat] = polyline[i];
        const [dLon, dLat] = polyline[i + 1];
        nmRemainingToStop += haversineNm(cLat, cLon, dLat, dLon);
      }
    }

    // ── 6. Bearing (cap) ─────────────────────────────────────────────────────
    // Use the SEGMENT direction (A→B endpoints) rather than snap→nextVertex.
    // Using snap→nextVertex causes bearing=atan2(0,0)=0° (north) whenever
    // the snap position coincides with the next vertex (e.g. t=1 at segment
    // end, or near-duplicate vertices like the Cap Corse routing artefact).
    let bearing = 0;
    if (bestSegIdx < polyline.length - 1) {
      const [aLon, aLat] = polyline[bestSegIdx];
      const [bLon, bLat] = polyline[bestSegIdx + 1];
      bearing = initialBearing(aLat, aLon, bLat, bLon);
    }

    // ── 7. ETA ───────────────────────────────────────────────────────────────
    const etaHours = speedKnots > 0 ? nmRemainingToStop / speedKnots : 0;

    return {
      fromStopIndex:     fromIdx,
      fromStop:          fromStop?.name ?? "Départ",
      toStopIndex:       toIdx,
      toStop:            toStop?.name  ?? "Arrivée",
      nmCovered:         Math.round(nmCoveredTotal),
      nmRemainingToStop: Math.round(nmRemainingToStop),
      etaHours:          Math.round(etaHours),
      bearing:           Math.round(bearing),
      snappedPosition:   [snapLon, snapLat],
      speedKnots,
    };
  }, [catamaranLat, catamaranLon, routeSegments, itineraryPoints, speedKnots]);
}
