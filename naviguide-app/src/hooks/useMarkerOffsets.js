/**
 * useMarkerOffsets
 * Calcule des décalages pixel pour éviter le chevauchement des markers MapLibre.
 *
 * Algorithme :
 *  1. Projette chaque point en coordonnées écran via map.project()
 *  2. Pour chaque paire de points dont la distance < THRESHOLD px,
 *     calcule un vecteur de répulsion et accumule l'offset
 *  3. Recompute à chaque fin de zoom ou de déplacement (debounced)
 *
 * Usage :
 *   const offsets = useMarkerOffsets(points, mapRef);
 *   // offsets[i] → [dx, dy] en pixels pour le marker i
 *   <Marker offset={offsets[i]} ... />
 */

import { useState, useEffect, useCallback, useRef } from "react";

const OVERLAP_THRESHOLD = 52; // px — distance min souhaitée entre centres
const DEBOUNCE_MS       = 120;

export function useMarkerOffsets(points, mapRef) {
  const [offsets, setOffsets] = useState(() => points.map(() => [0, 0]));
  const timerRef = useRef(null);

  const compute = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map || !points.length) return;

    // ── 1. Projection en pixels écran ────────────────────────────────────────
    const px = points.map((p) => {
      const pt = map.project([p.lon, p.lat]);
      return { x: pt.x, y: pt.y };
    });

    // ── 2. Accumulation des vecteurs de répulsion ─────────────────────────────
    const result = points.map(() => [0, 0]);

    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const dx   = px[j].x - px[i].x;
        const dy   = px[j].y - px[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < OVERLAP_THRESHOLD && dist > 0.5) {
          // Amplitude du décalage : moitié de la pénétration + marge de 3px
          const push = (OVERLAP_THRESHOLD - dist) / 2 + 3;
          const nx = dx / dist;
          const ny = dy / dist;
          result[i][0] -= nx * push;
          result[i][1] -= ny * push;
          result[j][0] += nx * push;
          result[j][1] += ny * push;
        }
      }
    }

    setOffsets(result);
  }, [points, mapRef]);

  // ── Debounced recompute on map move/zoom ──────────────────────────────────
  useEffect(() => {
    if (!points.length) return;

    const schedule = () => {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(compute, DEBOUNCE_MS);
    };

    // Attend que la carte soit chargée avant d'accrocher les events
    const attachListeners = () => {
      const map = mapRef.current?.getMap();
      if (!map) return false;
      map.on("zoomend", schedule);
      map.on("moveend", schedule);
      compute(); // calcul initial
      return true;
    };

    // Polling léger tant que la carte n'est pas prête
    if (!attachListeners()) {
      const poll = setInterval(() => {
        if (attachListeners()) clearInterval(poll);
      }, 200);
      return () => {
        clearInterval(poll);
        clearTimeout(timerRef.current);
      };
    }

    return () => {
      clearTimeout(timerRef.current);
      const map = mapRef.current?.getMap();
      if (map) {
        map.off("zoomend", schedule);
        map.off("moveend", schedule);
      }
    };
  }, [compute, mapRef, points]);

  return offsets;
}
