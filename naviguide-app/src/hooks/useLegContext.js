/**
 * useLegContext — Calcul géométrique du tronçon actif
 *
 * Snap-to-route : projection haversine manuelle sur chaque segment de la polyligne.
 * Aucun appel API, aucune dépendance externe (pas de @turf).
 *
 * Le paramètre simulationStep contraint le snap à la portion chronologiquement
 * correcte de la polyligne, évitant les ambiguïtés quand l'itinéraire passe
 * deux fois par la même zone géographique (ex: Cap Verde aller vs retour).
 *
 * Le mapping stops→polyligne est monotone (index[i] ≥ index[i-1]) pour respecter
 * l'ordre chronologique même quand deux stops partagent les mêmes coordonnées.
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
  const dx = bLon - aLon;
  const dy = bLat - aLat;
  const lenSq = dx * dx + dy * dy;

  let t = 0;
  if (lenSq > 0) {
    t = ((pLon - aLon) * dx + (pLat - aLat) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
  }

  const qLon = aLon + t * dx;
  const qLat = aLat + t * dy;
  return { point: [qLon, qLat], t, distNm: haversineNm(pLat, pLon, qLat, qLon) };
}

/**
 * Construit un mapping stops→polyline MONOTONE (index[i] ≥ index[i-1]).
 *
 * Contrairement à la recherche globale du plus proche voisin, cette approche
 * garantit que deux stops géographiquement identiques (ex: Cap Verde aller et
 * Cap Verde retour) sont mappés à des index polyligne différents et croissants,
 * respectant ainsi l'ordre chronologique de l'itinéraire.
 *
 * @param {Array} stops    — liste ordonnée de { lat, lon, name, ... }
 * @param {Array} polyline — tableau [[lon, lat], ...]
 * @returns {Array} — [{ stop, polyIdx }, ...]
 */
function buildMonotonicStopIndices(stops, polyline) {
  const result = [];
  let searchStart = 0;

  for (const stop of stops) {
    let best = searchStart;
    let bestD = Infinity;
    // Recherche uniquement à partir de searchStart (garantit la monotonie)
    for (let i = searchStart; i < polyline.length; i++) {
      const [pLon, pLat] = polyline[i];
      const d = haversineNm(stop.lat, stop.lon, pLat, pLon);
      if (d < bestD) { bestD = d; best = i; }
    }
    result.push({ stop, polyIdx: best });
    searchStart = best; // le prochain stop doit être au-delà de ce point
  }

  return result;
}

// ── Hook ─────────────────────────────────────────────────────────────────────

/**
 * @param {number|null} catamaranLat    — latitude du catamaran (null si non activé)
 * @param {number|null} catamaranLon    — longitude du catamaran
 * @param {Array}       routeSegments   — segments calculés par App.jsx [ {coords: [[lon,lat],...], nonMaritime?} ]
 * @param {Array}       itineraryPoints — ITINERARY_POINTS (escales + points intermédiaires)
 * @param {number}      speedKnots      — vitesse en nœuds (optionnel)
 * @param {number|null} simulationStep  — index dans simTargets (contraint le snap à la bonne portion)
 *
 * Layout de simTargets (généré par App.jsx) :
 *   step 0            → départ = début du segment maritime 0
 *   step 2k+1, k ≥ 0 → milieu du segment maritime k
 *   step 2k+2, k ≥ 0 → fin    du segment maritime k
 * Donc : maritimeSegIdx = (step === 0) ? 0 : Math.floor((step - 1) / 2)
 */
export function useLegContext(
  catamaranLat,
  catamaranLon,
  routeSegments,
  itineraryPoints,
  speedKnots = DEFAULT_SPEED_KNOTS,
  simulationStep = null,
) {
  return useMemo(() => {
    if (catamaranLat == null || catamaranLon == null) return null;
    if (!routeSegments || routeSegments.length === 0) return null;

    // ── 1. Flatten route segments + mémoriser les index de début par segment ─
    const polyline = [];
    // segPolyStart[i] = index dans polyline où commence routeSegments[i]
    const segPolyStart = [];

    for (let si = 0; si < routeSegments.length; si++) {
      const seg = routeSegments[si];
      segPolyStart.push(polyline.length); // enregistré AVANT d'ajouter les coords
      if (!seg.coords || seg.coords.length < 2) continue;
      if (polyline.length === 0) {
        polyline.push(...seg.coords);
      } else {
        // Skip first point of subsequent segments (duplicate junction)
        polyline.push(...seg.coords.slice(1));
      }
    }
    if (polyline.length < 2) return null;

    // ── 2. Fenêtre de snap contrainte par simulationStep ─────────────────────
    // Quand simulationStep est connu, on restreint la recherche du segment le
    // plus proche à la portion de polyligne correspondant au tronçon maritime
    // actuel (± 1 tronçon de tolérance). Ceci évite que le snap saute sur un
    // tronçon géographiquement proche mais chronologiquement différent.
    let snapWinStart = 0;
    let snapWinEnd   = polyline.length - 1;

    if (simulationStep !== null) {
      // Index des segments maritimes valides dans routeSegments
      const maritimeSegIndices = routeSegments
        .map((seg, i) => ({ seg, i }))
        .filter(({ seg }) => !seg.nonMaritime && seg.coords?.length >= 2)
        .map(({ i }) => i);

      if (maritimeSegIndices.length > 0) {
        // Segment maritime correspondant à simulationStep
        const msi = Math.min(
          simulationStep === 0 ? 0 : Math.floor((simulationStep - 1) / 2),
          maritimeSegIndices.length - 1,
        );

        // ±1 tronçon maritime de tolérance pour les transitions en douceur
        const msiFrom = Math.max(0, msi - 1);
        const msiTo   = Math.min(maritimeSegIndices.length - 1, msi + 1);

        const segIdxFrom = maritimeSegIndices[msiFrom];
        const segIdxTo   = maritimeSegIndices[msiTo];

        // Recule d'un point pour inclure le point de jonction entrant
        snapWinStart = Math.max(0, segPolyStart[segIdxFrom] - 1);

        // S'arrête au début du segment suivant (point de jonction sortant inclus)
        snapWinEnd = (segIdxTo + 1 < routeSegments.length)
          ? segPolyStart[segIdxTo + 1]
          : polyline.length - 1;

        snapWinStart = Math.max(0, snapWinStart);
        snapWinEnd   = Math.min(polyline.length - 1, snapWinEnd);
      }
    }

    // ── 3. Snap catamaran sur la fenêtre contrainte ───────────────────────────
    let bestDist   = Infinity;
    let bestSnap   = null;
    let bestSegIdx = snapWinStart;
    let bestT      = 0;

    for (let i = snapWinStart; i < snapWinEnd && i < polyline.length - 1; i++) {
      const [aLon, aLat] = polyline[i];
      const [bLon, bLat] = polyline[i + 1];
      const res = projectOnSegment(catamaranLat, catamaranLon, aLat, aLon, bLat, bLon);
      if (res.distNm < bestDist) {
        bestDist   = res.distNm;
        bestSnap   = res.point; // [lon, lat]
        bestSegIdx = i;
        bestT      = res.t;
      }
    }

    if (!bestSnap) return null;
    const [snapLon, snapLat] = bestSnap;

    // ── 4. Miles nautiques cumulés depuis le départ jusqu'au snap ────────────
    let nmCoveredTotal = 0;
    for (let i = 0; i < bestSegIdx; i++) {
      const [aLon, aLat] = polyline[i];
      const [bLon, bLat] = polyline[i + 1];
      nmCoveredTotal += haversineNm(aLat, aLon, bLat, bLon);
    }
    // Partie partielle du segment actif
    const [aLon, aLat] = polyline[bestSegIdx];
    const [bLon, bLat] = polyline[bestSegIdx + 1];
    nmCoveredTotal += bestT * haversineNm(aLat, aLon, bLat, bLon);

    // ── 5. Identification du tronçon actif (mapping monotone) ────────────────
    // Le mapping monotone garantit que stops identiques géographiquement mais
    // différents chronologiquement (ex: Cap Verde aller vs retour) sont mappés
    // à des index de polyligne distincts et croissants.
    const stops = (itineraryPoints || []);
    const stopIndices = buildMonotonicStopIndices(stops, polyline);

    let fromStop = stopIndices[0]?.stop;
    let toStop   = stopIndices[stopIndices.length - 1]?.stop;
    let fromIdx  = 0;
    let toIdx    = stopIndices.length - 1;

    for (let i = 0; i < stopIndices.length; i++) {
      if (stopIndices[i].polyIdx > bestSegIdx) {
        toStop   = stopIndices[i].stop;
        toIdx    = i;
        // Quand i=0 (catamaran avant la première escale), fromStop = null
        // pour afficher "Départ" au lieu de dupliquer le même stop en from ET to.
        fromStop = i > 0 ? stopIndices[i - 1].stop : null;
        fromIdx  = i > 0 ? i - 1 : 0;
        break;
      }
    }

    // ── 6. Miles nautiques restants jusqu'au prochain stop ───────────────────
    const toStopPolyIdx = stopIndices[toIdx]?.polyIdx ?? polyline.length - 1;
    let nmRemainingToStop = 0;
    if (toStopPolyIdx > bestSegIdx) {
      // Portion restante du segment actif
      nmRemainingToStop += (1 - bestT) * haversineNm(aLat, aLon, bLat, bLon);
      // Segments complets jusqu'au stop cible
      for (let i = bestSegIdx + 1; i < toStopPolyIdx; i++) {
        const [cLon, cLat] = polyline[i];
        const [dLon, dLat] = polyline[i + 1];
        nmRemainingToStop += haversineNm(cLat, cLon, dLat, dLon);
      }
    }

    // ── 7. Bearing (cap) ─────────────────────────────────────────────────────
    // Quand le snap est en fin de segment (t≈1, catamaran exactement sur une
    // escale/jonction), avancer d'un segment pour afficher le cap de DÉPART
    // plutôt que le cap d'ARRIVÉE. Exemple : catamaran AT Avant Corse doit
    // montrer le cap vers Ajaccio (NNE), pas le cap d'approche depuis La Rochelle.
    let bearingSegIdx = bestSegIdx;
    if (bestT > 0.999 && bearingSegIdx + 1 < polyline.length - 1) {
      bearingSegIdx += 1;
    }
    const [bearALon, bearALat] = polyline[bearingSegIdx];
    const [bearBLon, bearBLat] = polyline[bearingSegIdx + 1];
    const bearing = initialBearing(bearALat, bearALon, bearBLat, bearBLon);

    // ── 8. ETA ───────────────────────────────────────────────────────────────
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
  }, [catamaranLat, catamaranLon, routeSegments, itineraryPoints, speedKnots, simulationStep]);
}
