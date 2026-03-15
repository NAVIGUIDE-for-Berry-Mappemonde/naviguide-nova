import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Map, { Source, Layer, Marker, Popup } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { ITINERARY_POINTS } from "./constants/itineraryPoints";
import { X, Undo2, Redo2 } from "lucide-react";
import { WindDirectionArrow } from "./components/map/WindDirectionArrow";
import { getCardinalDirection } from "./utils/getCardinalDirection";
import { Sidebar } from "./components/Sidebar";
import { ExportSidebar } from "./components/ExportSidebar";
import { useLang } from "./i18n/LangContext.jsx";
import {
  useMaritimeLayers,
  MaritimeLayers,
  MaritimeLayersPanel,
  BalisageLayer,
} from "./components/MaritimeLayers";
import { useMarkerOffsets } from "./hooks/useMarkerOffsets";
import { CatamaranMarker } from "./components/CatamaranMarker";
import { useLegContext } from "./hooks/useLegContext";

const API_URL = import.meta.env.VITE_API_URL;
const ORCHESTRATOR_URL = import.meta.env.VITE_ORCHESTRATOR_URL;

// La Rochelle — position de départ du catamaran en mode simulation
const LA_ROCHELLE_POS = { lat: 46.1541, lon: -1.167 };

// ── Orchestrator plan cache (localStorage, 24 h TTL, per language) ───────────
const PLAN_CACHE_TTL = 24 * 60 * 60 * 1000; // 24 hours

function planCacheKey(lang) { return `naviguide_expedition_plan_v2_${lang}`; }

function getCachedPlan(lang) {
  try {
    const raw = localStorage.getItem(planCacheKey(lang));
    if (!raw) return null;
    const { data, ts } = JSON.parse(raw);
    if (Date.now() - ts > PLAN_CACHE_TTL) { localStorage.removeItem(planCacheKey(lang)); return null; }
    return data;
  } catch { return null; }
}

function setCachedPlan(lang, data) {
  try { localStorage.setItem(planCacheKey(lang), JSON.stringify({ data, ts: Date.now() })); } catch {}
}

const SEGMENT_BATCH_SIZE = 4; // legs fetched in parallel per batch

export default function App() {
  const { lang, t } = useLang();
  const mapRef = useRef(null);
  const [segments, setSegments] = useState([]);
  const [points, setPoints] = useState([]);
  const [loading, setLoading] = useState(true);
  // Progress: { done: number, total: number }
  const [segProgress, setSegProgress] = useState({ done: 0, total: 0 });
  const boundsApplied = useRef(false);

  // 🌊 Nouveau state pour les vagues
  const [selectedWave, setSelectedWave] = useState(null);
  const [waveLoading, setWaveLoading] = useState(false);

  // 🌊 State pour les courants
  const [selectedCurrent, setSelectedCurrent] = useState(null);
  const [currentLoading, setCurrentLoading] = useState(false);

  // Sidebar + orchestrator plan
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [expeditionPlan, setExpeditionPlan] = useState(null);

  // Export sidebar (right) — closed by default
  const [exportSidebarOpen, setExportSidebarOpen] = useState(false);

  // Polar data shared between ExportSidebar (upload/VMG) and Sidebar (chat)
  const [polarData, setPolarData] = useState(null);

  // ── App-wide modes ──────────────────────────────────────────────────────────
  const [isOffshore,  setIsOffshore]  = useState(true);  // always Offshore (toggles removed)
  const [isCockpit,   setIsCockpit]   = useState(false); // always Onboarding (toggles removed)
  const [isLightMode, setIsLightMode] = useState(false); // false=Dark, true=Light

  // ── Maritime data layers (ZEE, WPI Ports, SHOM Balisage) ────────────────────
  const maritimeLayers = useMaritimeLayers();

  // ── Simulation mode — catamaran draggable ────────────────────────────────────
  const [simulationMode, setSimulationMode] = useState(false);
  const [catamaranPos,   setCatamaranPos]   = useState(null);  // { lat, lon }
  const [simulationStep, setSimulationStep] = useState(0);

  // Position par défaut : La Rochelle (point de départ maritime de l'expédition)
  const initialCatamaranPos = LA_ROCHELLE_POS;
  const activeCatamaranPos  = catamaranPos ?? initialCatamaranPos;

  // Flat ordered list of simulation targets built from the REAL route polylines:
  //   [departure(step 0), mid(seg0), end(seg0), mid(seg1), end(seg1), …]
  // Using segments[] ensures midpoints lie ON the actual maritime route and
  // handles all detours (Saint-Pierre, Marigot→Cayenne, etc.) automatically.
  const simTargets = useMemo(() => {
    const maritimeSegs = segments.filter(s => !s.nonMaritime && s.coords?.length >= 2);
    if (maritimeSegs.length === 0) {
      return [{ lat: LA_ROCHELLE_POS.lat, lon: LA_ROCHELLE_POS.lon }];
    }
    // Step 0 = first coord of first maritime segment (La Rochelle departure)
    const firstCoord = maritimeSegs[0].coords[0];
    const list = [{ lat: firstCoord[1], lon: firstCoord[0] }];
    for (const seg of maritimeSegs) {
      const coords = seg.coords; // [[lon, lat], …]
      // Midpoint at 50% cumulative Euclidean distance along the polyline
      let totalLen = 0;
      const lengths = [];
      for (let i = 0; i < coords.length - 1; i++) {
        const l = Math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1]);
        lengths.push(l);
        totalLen += l;
      }
      const halfLen = totalLen / 2;
      let acc = 0;
      let mid = null;
      for (let i = 0; i < lengths.length; i++) {
        if (acc + lengths[i] >= halfLen) {
          const t = lengths[i] > 0 ? (halfLen - acc) / lengths[i] : 0;
          mid = {
            lat: coords[i][1] + t * (coords[i + 1][1] - coords[i][1]),
            lon: coords[i][0] + t * (coords[i + 1][0] - coords[i][0]),
          };
          break;
        }
        acc += lengths[i];
      }
      if (!mid) {
        const m = Math.floor(coords.length / 2);
        mid = { lat: coords[m][1], lon: coords[m][0] };
      }
      const last = coords[coords.length - 1];
      list.push(mid);
      list.push({ lat: last[1], lon: last[0] });
    }
    return list;
  }, [segments]);

  // flyTo helper — recenters map on catamaran with smooth animation
  const flyToPos = useCallback((lat, lon) => {
    if (!mapRef.current) return;
    const map = mapRef.current.getMap();
    if (map) map.flyTo({ center: [lon, lat], zoom: 8, duration: 800 });
  }, []);

  // Ref flag: true when Next/Prev was clicked and we're waiting for legContext to snap
  const pendingFlyTo = useRef(false);

  // Next step — advance one position forward in the flat target list
  const handleSimNext = useCallback(() => {
    const nextStep = simulationStep + 1;
    if (nextStep >= simTargets.length) return;
    const pos = simTargets[nextStep];
    setSimulationStep(nextStep);
    setCatamaranPos(pos);
    pendingFlyTo.current = true; // fly AFTER legContext snaps to the route
  }, [simulationStep, simTargets]);

  // Previous step — go back one position in the flat target list
  const handleSimPrev = useCallback(() => {
    const prevStep = simulationStep - 1;
    if (prevStep < 0) return;
    const pos = simTargets[prevStep];
    setSimulationStep(prevStep);
    setCatamaranPos(pos);
    pendingFlyTo.current = true; // fly AFTER legContext snaps to the route
  }, [simulationStep, simTargets]);

  // Manual drag — re-sync simulationStep to nearest target after snap
  const handleCatamaranDrag = useCallback((pos) => {
    setCatamaranPos(pos);
    let bestIdx = simulationStep;
    let bestDist = Infinity;
    simTargets.forEach((t, i) => {
      const d = Math.hypot(t.lat - pos.lat, t.lon - pos.lon);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    });
    setSimulationStep(bestIdx);
  }, [simulationStep, simTargets]);

  // Leg context : snap géométrique + métriques
  // simulationStep est passé pour contraindre le snap à la bonne portion de
  // polyligne — évite que le catamaran "saute" sur le tronçon retour quand
  // l'itinéraire passe deux fois par la même zone (ex: Cap Verde aller/retour).
  const legContext = useLegContext(
    simulationMode ? activeCatamaranPos.lat : null,
    simulationMode ? activeCatamaranPos.lon : null,
    segments,
    ITINERARY_POINTS,
    undefined,                               // speedKnots — valeur par défaut
    simulationMode ? simulationStep : null,  // contrainte chronologique
  );

  // After each Next/Prev step, fly to the SNAPPED position (not the raw target)
  // so the camera always centers on the boat as it appears on the route.
  useEffect(() => {
    if (!pendingFlyTo.current || !legContext) return;
    pendingFlyTo.current = false;
    flyToPos(legContext.snappedPosition[1], legContext.snappedPosition[0]);
  }, [legContext, flyToPos]);

  // ── Anti-overlap offsets pour les markers de drapeaux d'escales ──────────
  const markerOffsets = useMarkerOffsets(points, mapRef);

  // Custom imported route (null = show Berry-Mappemonde default route)
  const [customRoute, setCustomRoute] = useState(null); // GeoJSON FeatureCollection

  const handleRouteImport = (geojson) => setCustomRoute(geojson);
  const handleRouteSwitchToBerry = () => setCustomRoute(null);

  // ── Drawing mode ────────────────────────────────────────────────────────────
  const [drawingMode, setDrawingMode] = useState(false);
  const [drawnPoints, setDrawnPoints] = useState([]);     // [{lat, lon}, ...]
  const [drawnSegments, setDrawnSegments] = useState([]); // [{coords: [[lon,lat],...]}]
  const [drawingLoading, setDrawingLoading] = useState(false);
  const [canRedo, setCanRedo] = useState(false);           // drives Redo button enable state
  // Refs to avoid stale closures on rapid clicks
  const drawnPointsRef    = useRef([]);
  const drawnSegmentsRef  = useRef([]);
  // Undo/redo stacks
  const undonePointsRef   = useRef([]);
  const undoneSegmentsRef = useRef([]);
  // Fetch-id prevents stale async segments from landing after an undo
  const fetchIdRef        = useRef(0);

  const _resetDrawState = () => {
    setDrawnPoints([]);
    setDrawnSegments([]);
    setCanRedo(false);
    drawnPointsRef.current    = [];
    drawnSegmentsRef.current  = [];
    undonePointsRef.current   = [];
    undoneSegmentsRef.current = [];
    fetchIdRef.current        = 0;
  };

  const handleDrawStart = () => {
    setDrawingMode(true);
    _resetDrawState();
  };

  // Called from BerryCard "Finish" — returns FeatureCollection to BerryCard
  const handleDrawFinish = () => {
    // Route segments as LineStrings
    const lineFeatures = drawnSegmentsRef.current
      .filter((s) => s.coords?.length > 0)
      .map((s) => ({
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates: s.coords },
      }));
    // Drawn waypoints with metadata as Point features (only if name or flags set)
    const pointFeatures = drawnPointsRef.current
      .filter((p) => p.name || (p.flags && p.flags.length > 0))
      .map((p) => ({
        type: "Feature",
        properties: { name: p.name || "", flags: p.flags || [], naviguide_type: "drawn_waypoint" },
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      }));
    const geojson = { type: "FeatureCollection", features: [...lineFeatures, ...pointFeatures] };
    setDrawingMode(false);
    _resetDrawState();
    return geojson;
  };

  const fetchDrawnSegment = async (from, to) => {
    const myFetchId = ++fetchIdRef.current;
    setDrawingLoading(true);
    try {
      const params = new URLSearchParams({
        start_lat: from.lat, start_lon: from.lon,
        end_lat:   to.lat,   end_lon:   to.lon,
      });
      const res  = await fetch(`${API_URL}/route?${params}`);
      const data = await res.json();
      let coords = [];
      if (data.type === "FeatureCollection" && data.features?.length > 0) {
        coords = data.features[0].geometry?.coordinates || [];
      } else if (data.geometry?.coordinates) {
        coords = data.geometry.coordinates;
      }
      // Only apply result if this fetch wasn't superseded by an undo
      if (coords.length > 0 && fetchIdRef.current === myFetchId) {
        const updated = [...drawnSegmentsRef.current, { coords }];
        drawnSegmentsRef.current = updated;
        setDrawnSegments([...updated]);
      }
    } catch (err) {
      console.warn("Draw segment fetch error:", err);
    } finally {
      if (fetchIdRef.current === myFetchId) setDrawingLoading(false);
    }
  };

  const handleDrawingClick = (e) => {
    const { lng: lon, lat } = e.lngLat;
    const newPoint = { lat, lon };
    const updated = [...drawnPointsRef.current, newPoint];
    const newIdx  = updated.length - 1;
    drawnPointsRef.current    = updated;
    // New action clears the redo stack
    undonePointsRef.current   = [];
    undoneSegmentsRef.current = [];
    setCanRedo(false);
    setDrawnPoints([...updated]);
    if (updated.length >= 2) {
      fetchDrawnSegment(updated[updated.length - 2], newPoint);
    }
    // Auto-open satellite popup on the Point Info tab for this waypoint
    openSatelliteForDrawnPoint(lon, lat, newIdx);
  };

  const handleDrawUndo = () => {
    if (drawnPointsRef.current.length === 0) return;
    fetchIdRef.current++; // cancel any in-flight fetch for the removed point
    // Pop last point → undo stack
    const poppedPoint = drawnPointsRef.current[drawnPointsRef.current.length - 1];
    drawnPointsRef.current = drawnPointsRef.current.slice(0, -1);
    undonePointsRef.current = [...undonePointsRef.current, poppedPoint];
    // Pop last segment (if any) → undo stack
    if (drawnSegmentsRef.current.length > 0) {
      const poppedSeg = drawnSegmentsRef.current[drawnSegmentsRef.current.length - 1];
      drawnSegmentsRef.current = drawnSegmentsRef.current.slice(0, -1);
      undoneSegmentsRef.current = [...undoneSegmentsRef.current, poppedSeg];
    }
    setDrawnPoints([...drawnPointsRef.current]);
    setDrawnSegments([...drawnSegmentsRef.current]);
    setDrawingLoading(false);
    setCanRedo(true);
  };

  const handleDrawRedo = () => {
    if (undonePointsRef.current.length === 0) return;
    // Restore last undone point
    const restoredPoint = undonePointsRef.current[undonePointsRef.current.length - 1];
    undonePointsRef.current = undonePointsRef.current.slice(0, -1);
    drawnPointsRef.current = [...drawnPointsRef.current, restoredPoint];
    // Restore last undone segment (if any)
    if (undoneSegmentsRef.current.length > 0) {
      const restoredSeg = undoneSegmentsRef.current[undoneSegmentsRef.current.length - 1];
      undoneSegmentsRef.current = undoneSegmentsRef.current.slice(0, -1);
      drawnSegmentsRef.current = [...drawnSegmentsRef.current, restoredSeg];
    }
    setDrawnPoints([...drawnPointsRef.current]);
    setDrawnSegments([...drawnSegmentsRef.current]);
    setCanRedo(undonePointsRef.current.length > 0);
  };

  const drawingMessage =
    drawnPoints.length === 0 ? t("drawStart") :
    drawnPoints.length === 1 ? t("drawFirstStop") :
    t("drawNextStop");

  // Hover state for itinerary stop markers
  const [hoveredPoint, setHoveredPoint] = useState(null);

  // Clipboard toast
  const [clipboardToast, setClipboardToast] = useState(null);

  // Route-click satellite data popup
  const [selectedSatellite, setSelectedSatellite] = useState(null);
  const [satelliteLoading, setSatelliteLoading] = useState(false);
  const [satelliteTab, setSatelliteTab] = useState("wind");
  const [routeCursor, setRouteCursor] = useState("crosshair");

  // ── Point Info state (for drawn waypoints) ─────────────────────────────────
  const [pointInfoName, setPointInfoName]   = useState("");
  const [pointInfoFlags, setPointInfoFlags] = useState([null, null]);

  /** Open satellite popup + Point Info tab for a newly placed drawn waypoint */
  const openSatelliteForDrawnPoint = async (lon, lat, drawPointIndex) => {
    const existing = drawnPointsRef.current[drawPointIndex];
    setPointInfoName(existing?.name  || "");
    setPointInfoFlags([existing?.flags?.[0] ?? null, existing?.flags?.[1] ?? null]);
    setSatelliteLoading(true);
    setSatelliteTab("point");
    setSelectedSatellite({ lon, lat, wind: null, wave: null, current: null, drawPointIndex });

    const fetchJson = (endpoint) =>
      fetch(`${API_URL}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ latitude: lat, longitude: lon }),
      }).then((r) => (r.ok ? r.json() : null)).catch(() => null);

    const [windResult, waveResult, currentResult] = await Promise.allSettled([
      fetchJson("wind"),
      fetchJson("wave"),
      fetchJson("current"),
    ]);

    setSelectedSatellite({
      lon, lat,
      wind:    windResult.status    === "fulfilled" ? windResult.value    : null,
      wave:    waveResult.status    === "fulfilled" ? waveResult.value    : null,
      current: currentResult.status === "fulfilled" ? currentResult.value : null,
      drawPointIndex,
    });
    setSatelliteLoading(false);
  };

  /** Save Point Info metadata (name + flags) to the drawn waypoint */
  const handleSaveDrawPointMeta = () => {
    if (selectedSatellite?.drawPointIndex != null) {
      const idx = selectedSatellite.drawPointIndex;
      const updated = [...drawnPointsRef.current];
      updated[idx] = {
        ...updated[idx],
        name:  pointInfoName.trim() || undefined,
        flags: pointInfoFlags.filter(Boolean),
      };
      drawnPointsRef.current = updated;
      setDrawnPoints([...updated]);
    }
    setSelectedSatellite(null);
  };

  // Fetch orchestrator plan — serve from localStorage cache instantly, refresh in background.
  // Re-fetches when language changes to get briefing in the selected language.
  useEffect(() => {
    const cached = getCachedPlan(lang);
    if (cached) {
      setExpeditionPlan(cached);                             // instant render from cache
    } else {
      setExpeditionPlan(null);                              // clear stale plan from previous language
    }
    if (!ORCHESTRATOR_URL) return;
    fetch(`${ORCHESTRATOR_URL}/api/v1/expedition/plan/berry-mappemonde`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language: lang,
        waypoints: ITINERARY_POINTS.map(p => ({
          name: p.name,
          lat:  p.lat,
          lon:  p.lon,
          type: p.flag ? "escale_obligatoire" : "point_intermediaire",
        })),
      }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data?.expedition_plan) {
          setExpeditionPlan(data.expedition_plan);
          setCachedPlan(lang, data.expedition_plan);         // persist per language
        }
      })
      .catch((err) => console.warn("Orchestrator unavailable:", err));
  }, [lang]);

  // Points d'intérêt
  useEffect(() => {
    setPoints(ITINERARY_POINTS);
  }, []);

  // Fetch des segments
  useEffect(() => {
    if (points.length === 0) return;

    // Helper: find a point by name (robust to index changes)
    const byName = (name) => points.find((p) => p.name === name);

    // Non-maritime segments (road/air only)
    // Halifax→SPM = segment MARITIME découplé → calculé via searoute, affiché en bleu
    const nonMaritimeNames = new Set([
      "Saint-Maur (Berry, Indre)|La Rochelle",
      "La Rochelle|Saint-Maur (Berry, Indre)",
    ]);

    // Points dont la liaison séquentielle est remplacée par des legs personnalisés
    // Marigot→Cayenne, Halifax→SPM, SPM→Halifax, Cayenne→Papeete (pas de segment Cayenne↔Halifax)
    const skipFromNames = new Set([
      "Marigot (Saint-Martin)",
      "Cayenne (Guyane)",
      "Halifax (Nouvelle-Écosse)",
      "Saint-Pierre (Saint-Pierre-et-Miquelon)",
    ]);

    const legs = [];

    for (let i = 0; i < points.length - 1; i++) {
      if (skipFromNames.has(points[i].name)) continue;
      const a = points[i];
      const b = points[i + 1];
      legs.push({ from: a, to: b });
    }

    // Insert Marigot → Cayenne après la leg qui arrive à Marigot
    const marigotIdx = legs.findIndex((l) => l.to.name === "Marigot (Saint-Martin)");
    legs.splice(marigotIdx + 1, 0, {
      from: byName("Marigot (Saint-Martin)"),
      to: byName("Cayenne (Guyane)"),
    });

    // Après Cayenne : Halifax → SPM → Halifax (air, non symbolisé), puis Cayenne → Papeete (maritime).
    // Pas de segment Cayenne↔Halifax : les trajets aériens ne sont pas ajoutés ni affichés.
    const cayenneIdx = legs.findIndex((l) => l.to.name === "Cayenne (Guyane)");
    legs.splice(cayenneIdx + 1, 0,
      { from: byName("Halifax (Nouvelle-Écosse)"), to: byName("Saint-Pierre (Saint-Pierre-et-Miquelon)") },
      { from: byName("Saint-Pierre (Saint-Pierre-et-Miquelon)"), to: byName("Halifax (Nouvelle-Écosse)") },
      { from: byName("Cayenne (Guyane)"), to: byName("Papeete (Polynésie française)") },
    );

    // ── Segment direction guard ──────────────────────────────────────────────
    // searoute may return coords in either direction (A→B or B→A).
    // Ensure the polyline always runs from leg.from → leg.to so that the
    // bearing calculation (A→B on each sub-segment) points the right way.
    const sqDist = (coord, point) => {
      const dLon = coord[0] - point.lon;
      const dLat = coord[1] - point.lat;
      return dLon * dLon + dLat * dLat;
    };
    const orientCoords = (coords, from, to) => {
      if (!coords || coords.length < 2) return coords;
      // If coords[0] is closer to `to` than to `from`, the array is reversed
      return sqDist(coords[0], to) < sqDist(coords[0], from)
        ? [...coords].reverse()
        : coords;
    };

    const fetchLeg = async (leg) => {
      const legKey = `${leg.from.name}|${leg.to.name}`;
      const isNonMaritime = nonMaritimeNames.has(legKey);

      if (isNonMaritime) {
        return {
          ...leg,
          coords: [
            [leg.from.lon, leg.from.lat],
            [leg.to.lon, leg.to.lat],
          ],
          nonMaritime: true,
        };
      }

      try {
        const params = new URLSearchParams({
          start_lat: leg.from.lat,
          start_lon: leg.from.lon,
          end_lat: leg.to.lat,
          end_lon: leg.to.lon,
          check_wind: false,
        });
        const res = await fetch(`${API_URL}/route?${params}`);
        const data = await res.json();

        if (data.type === "FeatureCollection" && data.features.length > 0) {
          const routeFeature = data.features[0];
          const alertPoints = data.features.slice(1);

          const windPoints = alertPoints.filter((p) => p.properties.highWind);
          const wavePoints = alertPoints.filter((p) => p.properties.highWave);
          const currentPoints = alertPoints.filter(
            (p) => p.properties.currents
          ); // 👈 ICI

          if (routeFeature.geometry && routeFeature.geometry.coordinates) {
            return {
              ...leg,
              coords: orientCoords(routeFeature.geometry.coordinates, leg.from, leg.to),
              windPoints,
              wavePoints,
              currentPoints, // 👈 Et on les ajoute ici
              nonMaritime: false,
            };
          }
        } else if (data.geometry && data.geometry.coordinates) {
          return {
            ...leg,
            coords: orientCoords(data.geometry.coordinates, leg.from, leg.to),
            windPoints: [],
            wavePoints: [],
            nonMaritime: false,
          };
        }
      } catch (e) {
        return {
          ...leg,
          coords: [],
          windPoints: [],
          wavePoints: [],
          error: e.message,
        };
      }
    };

    (async () => {
      boundsApplied.current = false;
      setLoading(true);
      setSegProgress({ done: 0, total: legs.length });

      const accumulated = [];

      for (let i = 0; i < legs.length; i += SEGMENT_BATCH_SIZE) {
        const batch = legs.slice(i, i + SEGMENT_BATCH_SIZE);
        const batchResults = await Promise.all(batch.map(fetchLeg));
        accumulated.push(...batchResults);

        const valid = accumulated.filter((r) => r && r.coords && r.coords.length > 0);
        setSegments([...valid]);
        setSegProgress({ done: Math.min(i + SEGMENT_BATCH_SIZE, legs.length), total: legs.length });

        // Reveal the map after the first batch so the screen unlocks immediately
        if (i === 0) setLoading(false);
      }

      setLoading(false);
    })();
  }, [points]);

  // Fit bounds — once, when all segments have arrived
  useEffect(() => {
    if (!mapRef.current || segments.length === 0) return;
    // Only fit once per full load (not on every progressive batch update)
    if (boundsApplied.current) return;
    if (segProgress.done < segProgress.total && segProgress.total > 0) return;
    boundsApplied.current = true;

    const map = mapRef.current.getMap();
    const allCoords = segments.flatMap((s) => s.coords);
    const lons = allCoords.map((c) => c[0]);
    const lats = allCoords.map((c) => c[1]);

    map.fitBounds(
      [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      { padding: 80, duration: 1200 }
    );
  }, [segments, segProgress]);

  // ── Route-click: fetch satellite data for any clicked point on the route ────
  const handleRouteClick = async (e) => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    // Only trigger if click lands on the maritime route line
    const features = map.queryRenderedFeatures(e.point, {
      layers: ["maritime-layer"],
    });
    if (features.length === 0) return;

    const lon = e.lngLat.lng;
    const lat = e.lngLat.lat;

    setSatelliteLoading(true);
    setSatelliteTab("wind");
    setSelectedSatellite({ lon, lat, wind: null, wave: null, current: null });

    const fetchJson = (endpoint) =>
      fetch(`${API_URL}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ latitude: lat, longitude: lon }),
      }).then((r) => (r.ok ? r.json() : null)).catch(() => null);

    const [windResult, waveResult, currentResult] = await Promise.allSettled([
      fetchJson("wind"),
      fetchJson("wave"),
      fetchJson("current"),
    ]);

    setSelectedSatellite({
      lon,
      lat,
      wind:    windResult.status    === "fulfilled" ? windResult.value    : null,
      wave:    waveResult.status    === "fulfilled" ? waveResult.value    : null,
      current: currentResult.status === "fulfilled" ? currentResult.value : null,
    });
    setSatelliteLoading(false);
  };

  // Construction GeoJSON — use customRoute (FeatureCollection) when a file has been imported
  // Live drawn route (green) shown during drawing mode
  const drawnLines = {
    type: "FeatureCollection",
    features: drawnSegments
      .filter((s) => s.coords?.length > 0)
      .map((s) => ({
        type: "Feature",
        geometry: { type: "LineString", coordinates: s.coords },
      })),
  };

  const EMPTY_FC = { type: "FeatureCollection", features: [] };

  // Hide all existing routes while the user is actively drawing (segments stay in memory)
  const maritimeLines = drawingMode
    ? EMPTY_FC
    : customRoute
      ? customRoute
      : {
          type: "FeatureCollection",
          features: segments
            .filter((s) => !s.nonMaritime)
            .map((s) => ({
              type: "Feature",
              geometry: { type: "LineString", coordinates: s.coords },
            })),
        };

  const nonMaritimeLines = drawingMode
    ? EMPTY_FC
    : customRoute
      ? EMPTY_FC
      : {
          type: "FeatureCollection",
          features: segments
            .filter((s) => s.nonMaritime)
            .map((s) => ({
              type: "Feature",
              geometry: { type: "LineString", coordinates: s.coords },
            })),
        };

  return (
    <div
      style={{ height: "100vh", width: "100vw", position: "relative" }}
      className={[isLightMode ? "light-mode" : "", isOffshore ? "offshore-mode" : ""].filter(Boolean).join(" ")}
    >
      <Sidebar
        plan={expeditionPlan}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((o) => !o)}
        onRouteImport={handleRouteImport}
        onRouteSwitchToBerry={handleRouteSwitchToBerry}
        isDrawing={drawingMode}
        onDrawStart={handleDrawStart}
        onDrawFinish={handleDrawFinish}
        isCockpit={isCockpit}
        isOffshore={isOffshore}
        polarData={polarData}
        maritimeLayers={maritimeLayers}
        simulationMode={simulationMode}
        onSimulationToggle={() => {
          const entering = !simulationMode;
          setSimulationMode(entering);
          if (entering) {
            // Activation : positionner le catamaran sur La Rochelle
            setCatamaranPos(LA_ROCHELLE_POS);
            setSimulationStep(0);
          } else {
            setCatamaranPos(null);
          }
        }}
        onNext={handleSimNext}
        canNext={simulationMode && simulationStep < simTargets.length - 1}
        onPrev={handleSimPrev}
        canPrev={simulationMode && simulationStep > 0}
        legContext={legContext}
      />
      <ExportSidebar
        segments={segments}
        points={points}
        open={exportSidebarOpen}
        onToggle={() => setExportSidebarOpen((o) => !o)}
        isOffshore={isOffshore}
        isCockpit={isCockpit}
        isLightMode={isLightMode}
        onOffshoreChange={setIsOffshore}
        onCockpitChange={setIsCockpit}
        onLightModeChange={setIsLightMode}
        polarData={polarData}
        onPolarDataLoaded={setPolarData}
      />

      {/* ── Slim loading phase: first-batch spinner, disappears quickly ───── */}
      {loading && (
        <div className="absolute inset-0 bg-slate-900/80 backdrop-blur-sm flex flex-col items-center justify-center z-10 pointer-events-none">
          <div className="w-10 h-10 border-4 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
          <div className="mt-4 text-white/90 text-sm font-medium tracking-wide">
            {t("calculatingRoutes")}
          </div>
        </div>
      )}

      {/* ── Progress pill — stays visible while remaining batches load ─────── */}
      {!loading && segProgress.done < segProgress.total && (
        <div className="absolute bottom-5 right-5 z-20 flex items-center gap-2 bg-slate-900/90 text-white text-xs font-medium px-3 py-2 rounded-full shadow-lg pointer-events-none">
          <div className="w-3.5 h-3.5 border-2 border-blue-400/40 border-t-blue-400 rounded-full animate-spin" />
          <span>{t("routesProgress", { done: segProgress.done, total: segProgress.total })}</span>
          {/* slim progress bar */}
          <div className="w-20 h-1.5 bg-white/20 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-400 rounded-full transition-all duration-500"
              style={{ width: `${(segProgress.done / segProgress.total) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* ── Drawing mode prompt + Undo/Redo ────────────────────────────── */}
      {drawingMode && (
        <div className="absolute top-16 left-1/2 -translate-x-1/2 z-30 pointer-events-none">
          <div className="flex items-center gap-2 bg-slate-900/95 border border-green-500/50 text-white text-sm font-semibold px-4 py-2.5 rounded-full shadow-2xl">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse flex-shrink-0" />
            <span className="mr-1">{drawingMessage}</span>
            {drawingLoading && (
              <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            )}
            {/* Undo / Redo — pointer-events-auto so clicks reach these buttons */}
            <div className="flex gap-1 ml-1 pointer-events-auto">
              <button
                onClick={handleDrawUndo}
                disabled={drawnPoints.length === 0}
                className={`w-7 h-7 flex items-center justify-center rounded-full bg-white/10 transition-colors
                  ${drawnPoints.length === 0 ? "opacity-30 cursor-not-allowed" : "hover:bg-white/25 cursor-pointer"}`}
                title={t("undoLastPoint")}
              >
                <Undo2 size={13} />
              </button>
              <button
                onClick={handleDrawRedo}
                disabled={!canRedo}
                className={`w-7 h-7 flex items-center justify-center rounded-full bg-white/10 transition-colors
                  ${!canRedo ? "opacity-30 cursor-not-allowed" : "hover:bg-white/25 cursor-pointer"}`}
                title={t("redo")}
              >
                <Redo2 size={13} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Clipboard toast ─────────────────────────────────────────────── */}
      {clipboardToast && (
        <div className="absolute top-5 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 bg-slate-900/95 text-white text-xs font-medium px-4 py-2 rounded-full shadow-lg pointer-events-none animate-fadeIn">
          <span>📋</span>
          <span>{clipboardToast} {t("copied")}</span>
        </div>
      )}

      <Map
        ref={mapRef}
        initialViewState={{ latitude: 0, longitude: 10, zoom: 1.5 }}
        style={{ width: "100%", height: "100%" }}
        mapStyle="https://demotiles.maplibre.org/style.json"
        doubleClickZoom={false}
        dragRotate={false}
        touchZoomRotate={false}
        cursor={drawingMode ? "crosshair" : routeCursor}
        interactiveLayerIds={drawingMode ? [] : ["maritime-layer"]}
        onClick={(e) => { if (drawingMode) { handleDrawingClick(e); } else { handleRouteClick(e); } }}
        onMouseEnter={() => { if (!drawingMode) setRouteCursor("pointer"); }}
        onMouseLeave={() => { if (!drawingMode) setRouteCursor("crosshair"); }}
        onLoad={(event) => {
          const map = event.target;
          const arrowSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#0077ff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-chevron-right-icon lucide-chevron-right"><path d="m9 18 6-6-6-6"/></svg>          `;

          const img = new Image(30, 30);
          img.onload = () => {
            if (!map.hasImage("arrow")) {
              map.addImage("arrow", img);
            }
          };
          img.src = "data:image/svg+xml;base64," + btoa(arrowSvg);
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          const { lng, lat } = e.lngLat;
          const text = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
          navigator.clipboard.writeText(text).then(() => {
            setClipboardToast(text);
            setTimeout(() => setClipboardToast(null), 2000);
          });
        }}
      >
        {/* ── Maritime data layers (ZEE / Ports / Balisage) — AVANT les routes pour être en dessous ── */}
        <MaritimeLayers
          showZee={maritimeLayers.showZee}
          showPorts={maritimeLayers.showPorts}
          portsData={maritimeLayers.portsData}
          showBalisage={maritimeLayers.showBalisage}
        />

        {/* Lignes maritimes */}
        <Source id="maritime" type="geojson" data={maritimeLines}>
          <Layer
            id="maritime-layer"
            type="line"
            paint={{
              "line-color": "#0077ff",
              "line-width": 3,
              "line-opacity": 0.9,
            }}
          />
          <Layer
            id="maritime-arrows"
            type="symbol"
            layout={{
              "symbol-placement": "line",
              "symbol-spacing": 100,
              "icon-image": "arrow",
              "icon-size": 0.8,
              "icon-rotation-alignment": "map",
              "icon-allow-overlap": true,
              "icon-ignore-placement": true,
            }}
          />
        </Source>

        {/* Lignes non maritimes */}
        <Source id="non-maritime" type="geojson" data={nonMaritimeLines}>
          <Layer
            id="non-maritime-layer"
            type="line"
            paint={{
              "line-color": "orange",
              "line-width": 4,
              "line-dasharray": [2, 2],
            }}
          />
        </Source>

        {/* ── Drawn route — live green line during drawing mode ────────── */}
        <Source id="drawn-route" type="geojson" data={drawnLines}>
          <Layer
            id="drawn-route-layer"
            type="line"
            paint={{ "line-color": "#22c55e", "line-width": 3, "line-opacity": 0.95 }}
          />
        </Source>

        {/* ── Drawn waypoint markers ────────────────────────────────────── */}
        {drawingMode && drawnPoints.map((p, i) => (
          <Marker key={`draw-pt-${i}`} longitude={p.lon} latitude={p.lat}>
            <div style={{
              width:           i === 0 ? 14 : 11,
              height:          i === 0 ? 14 : 11,
              borderRadius:    "50%",
              backgroundColor: i === 0 ? "#22c55e" : "#60a5fa",
              border:          `${i === 0 ? "2.5px" : "2px"} solid white`,
              boxShadow:       i === 0
                ? "0 0 6px rgba(34,197,94,0.7)"
                : "0 0 4px rgba(96,165,250,0.6)",
            }} title={i === 0 ? t("startingPoint") : `${t("stop")} ${i}`} />
          </Marker>
        ))}

        {/* Points de vent fort — visibles uniquement en mode Offshore */}
        {isOffshore && segments.flatMap((s, segIdx) =>
          (s.windPoints || []).map((point, i) => {
            const [lon, lat] = point.geometry.coordinates;
            const hasHighWave = point.properties.highWave;

            return (
              <Marker key={`wind-${segIdx}-${i}`} longitude={lon} latitude={lat}>
                <div
                  className={`wind-alert-marker${hasHighWave ? " is-wind-wave" : ""}`}
                  title={hasHighWave ? t("strongWindWave") : t("strongWind")}
                />
              </Marker>
            );
          })
        )}

        {/* 🌊 Points de vagues hautes uniquement (orange) — mode Offshore seulement */}
        {isOffshore && segments.flatMap((s, segIdx) =>
          (s.wavePoints || [])
            .filter((point) => !point.properties.highWind) // Seulement ceux sans vent fort
            .map((point, i) => {
              const [lon, lat] = point.geometry.coordinates;

              const handleWaveClick = async () => {
                setWaveLoading(true);
                setSelectedWave({
                  longitude: lon,
                  latitude: lat,
                  data: null,
                });

                try {
                  const res = await fetch(`${API_URL}/wave`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ latitude: lat, longitude: lon }),
                  });

                  if (!res.ok) throw new Error(t("waveApiError"));

                  const data = await res.json();

                  setSelectedWave({
                    longitude: lon,
                    latitude: lat,
                    data,
                  });
                } catch (err) {
                  console.error(err);
                  setSelectedWave({
                    longitude: lon,
                    latitude: lat,
                    error: t("waveDataError"),
                  });
                } finally {
                  setWaveLoading(false);
                }
              };

              return (
                <Marker
                  key={`wave-${segIdx}-${i}`}
                  longitude={lon}
                  latitude={lat}
                >
                  <div
                    onClick={handleWaveClick}
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: "50%",
                      backgroundColor: "orange",
                      border: "2px solid white",
                      boxShadow: "0 0 5px rgba(255,165,0,0.5)",
                      cursor: "pointer",
                    }}
                    title={t("highWaves")}
                  />
                </Marker>
              );
            })
        )}

        {/* 🔀 Courants — mode Offshore seulement */}
        {isOffshore && segments.flatMap((s, segIdx) =>
          (s.currentPoints || []).map((point, i) => {
            const [lon, lat] = point.geometry.coordinates;
            const currentData = point.properties.currents;
            const waveData = point.properties.highWave;
            const windData = point.properties.highWind;

            // 👉 Ne rien afficher si un point de vent ou de vague existe
            if (waveData || windData) return null;

            const rotation = currentData?.direction_deg || 0;

            const handleCurrentClick = async () => {
              setCurrentLoading(true);
              setSelectedCurrent({
                longitude: lon,
                latitude: lat,
                data: currentData,
              });
              setTimeout(() => setCurrentLoading(false), 300);
            };

            return (
              <Marker
                key={`current-${segIdx}-${i}`}
                longitude={lon}
                latitude={lat}
              >
                <div
                  onClick={handleCurrentClick}
                  style={{
                    width: 30,
                    height: 30,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    cursor: "pointer",
                    transform: `rotate(${rotation}deg)`,
                    filter: "drop-shadow(0 0 3px rgba(34,197,94,0.5))",
                  }}
                  title={t("currentSpeed", { speed: currentData?.speed_knots?.toFixed(2) })}
                >
                  <svg
                    width="100"
                    height="100"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#22c55e"
                    strokeWidth="4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M12 5v14M19 12l-7 7-7-7" />
                  </svg>
                </div>
              </Marker>
            );
          })
        )}

        {/* 🌊 Popup vagues */}
        {selectedWave && (
          <Popup
            longitude={selectedWave.longitude}
            latitude={selectedWave.latitude}
            closeButton={false}
            closeOnClick={false}
            anchor="top"
            offset={25}
            onClose={() => setSelectedWave(null)}
            className="!bg-transparent !border-none !shadow-none custom-popup"
          >
            <div className="bg-white rounded-xl shadow-2xl overflow-hidden min-w-[240px] animate-fadeIn">
              <div className="bg-gradient-to-r from-orange-500 to-orange-600 px-4 py-3 flex items-center justify-between">
                <h4 className="text-white font-semibold text-sm flex items-center gap-2">
                  <span>Wave Data</span>
                </h4>
                <button
                  onClick={() => setSelectedWave(null)}
                  className="text-white/80 hover:text-white hover:bg-white/20 rounded w-6 h-6 flex items-center justify-center transition-colors font-bold"
                >
                  <X />
                </button>
              </div>

              <div className="p-4">
                {waveLoading ? (
                  <div className="flex flex-col items-center py-5">
                    <div className="w-8 h-8 border-4 border-orange-100 border-t-orange-600 rounded-full animate-spin" />
                    <div className="mt-3 text-slate-500 text-sm">
                      Loading...
                    </div>
                  </div>
                ) : selectedWave.error ? (
                  <div className="flex items-center gap-3 p-3 bg-red-50 border border-red-200 rounded-lg">
                    <span className="text-xl">⚠️</span>
                    <div className="text-red-600 text-sm">
                      {selectedWave.error}
                    </div>
                  </div>
                ) : selectedWave.data ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg">
                          🌊
                        </div>
                        <div>
                          <div className="text-xs text-slate-500 mb-0.5">
                            Significant Height
                          </div>
                          <div className="text-base font-semibold text-slate-800">
                            {selectedWave.data.significant_wave_height_m} m
                          </div>
                        </div>
                      </div>
                    </div>

                    {selectedWave.data.mean_wave_period && (
                      <div className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                        <div className="flex items-center gap-2">
                          <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg">
                            ⏱️
                          </div>
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">
                              Period
                            </div>
                            <div className="text-base font-semibold text-slate-800">
                              {selectedWave.data.mean_wave_period} s
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {selectedWave.data.mean_wave_direction && (
                      <div className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                        <div className="flex items-center gap-2">
                          <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg">
                            🧭
                          </div>
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">
                              Direction
                            </div>
                            <div className="text-base font-semibold text-slate-800">
                              {selectedWave.data.mean_wave_direction}°
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="py-5 text-center text-slate-500 text-sm">
                    No data available
                  </div>
                )}
              </div>
            </div>
          </Popup>
        )}

        {selectedCurrent && (
          <Popup
            longitude={selectedCurrent.longitude}
            latitude={selectedCurrent.latitude}
            closeButton={false}
            closeOnClick={false}
            anchor="top"
            offset={25}
            onClose={() => setSelectedCurrent(null)}
            className="!bg-transparent !border-none !shadow-none custom-popup"
          >
            <div className="bg-white rounded-xl shadow-2xl overflow-hidden min-w-[240px] animate-fadeIn">
              <div className="bg-gradient-to-r from-green-500 to-green-600 px-4 py-3 flex items-center justify-between">
                <h4 className="text-white font-semibold text-sm flex items-center gap-2">
                  <span>Current Data</span>
                </h4>
                <button
                  onClick={() => setSelectedCurrent(null)}
                  className="text-white/80 hover:text-white hover:bg-white/20 rounded w-6 h-6 flex items-center justify-center transition-colors font-bold"
                >
                  <X />
                </button>
              </div>

              <div className="p-4">
                {currentLoading ? (
                  <div className="flex flex-col items-center py-5">
                    <div className="w-8 h-8 border-4 border-green-100 border-t-green-600 rounded-full animate-spin" />
                    <div className="mt-3 text-slate-500 text-sm">
                      Loading...
                    </div>
                  </div>
                ) : selectedCurrent.error ? (
                  <div className="flex items-center gap-3 p-3 bg-red-50 border border-red-200 rounded-lg">
                    <span className="text-xl">⚠️</span>
                    <div className="text-red-600 text-sm">
                      {selectedCurrent.error}
                    </div>
                  </div>
                ) : selectedCurrent.data ? (
                  <div className="space-y-3">
                    {/* 💨 Vitesse */}
                    <div className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg">
                          🌊
                        </div>
                        <div>
                          <div className="text-xs text-slate-500 mb-0.5">
                            Speed
                          </div>
                          <div className="text-base font-semibold text-slate-800">
                            {selectedCurrent.data.speed_knots.toFixed(2)} kn
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* 🧭 Direction */}
                    {selectedCurrent.data.direction_deg && (
                      <div className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                        <div className="flex items-center gap-2">
                          <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg">
                            🧭
                          </div>
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">
                              Direction
                            </div>
                            <div className="text-base font-semibold text-slate-800">
                              {selectedCurrent.data.direction_deg.toFixed(1)}°
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="py-5 text-center text-slate-500 text-sm">
                    No data available
                  </div>
                )}
              </div>
            </div>
          </Popup>
        )}

        {/* ── Satellite data popup — triggered by clicking the route ─────── */}
        {selectedSatellite && (
          <Popup
            longitude={selectedSatellite.lon}
            latitude={selectedSatellite.lat}
            closeButton={false}
            closeOnClick={false}
            anchor="top"
            offset={20}
            onClose={() => setSelectedSatellite(null)}
            className="!bg-transparent !border-none !shadow-none custom-popup"
          >
            <div className="bg-white rounded-xl shadow-2xl overflow-hidden animate-fadeIn" style={{ minWidth: 270 }}>
              {/* Header */}
              <div className="bg-gradient-to-r from-slate-700 to-slate-800 px-4 py-3 flex items-center justify-between">
                <div>
                  <div className="text-white font-semibold text-sm">{t("satelliteData")}</div>
                  <div className="text-slate-400 text-xs mt-0.5">
                    {selectedSatellite.lat.toFixed(3)}°, {selectedSatellite.lon.toFixed(3)}°
                  </div>
                </div>
                <button
                  onClick={() => setSelectedSatellite(null)}
                  className="text-white/80 hover:text-white hover:bg-white/20 rounded w-6 h-6 flex items-center justify-center transition-colors"
                >
                  <X size={14} />
                </button>
              </div>

              {/* Tabs */}
              {/* ── Tabs ──────────────────────────────────────────────── */}
              <div className="flex border-b border-slate-200">
                {[
                  { key: "wind",    label: t("windTab"),     active: "text-blue-600 border-b-2 border-blue-600 bg-blue-50" },
                  { key: "wave",    label: t("wavesTab"),    active: "text-orange-500 border-b-2 border-orange-500 bg-orange-50" },
                  { key: "current", label: t("currentsTab"), active: "text-emerald-600 border-b-2 border-emerald-600 bg-emerald-50" },
                  ...(selectedSatellite?.drawPointIndex != null
                    ? [{ key: "point", label: t("pointTab"), active: "text-violet-600 border-b-2 border-violet-600 bg-violet-50" }]
                    : []),
                ].map(({ key, label, active }) => (
                  <button
                    key={key}
                    onClick={() => setSatelliteTab(key)}
                    className={`flex-1 py-2 text-xs font-semibold transition-colors ${
                      satelliteTab === key ? active : "text-slate-500 hover:text-slate-700"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* ── Content ───────────────────────────────────────────── */}
              <div className="p-4">
                {satelliteLoading ? (
                  <div className="flex flex-col items-center py-5">
                    <div className="w-8 h-8 border-4 border-slate-100 border-t-slate-600 rounded-full animate-spin" />
                    <div className="mt-3 text-slate-500 text-sm">{t("fetchingSatellite")}</div>
                  </div>

                ) : satelliteTab === "wind" ? (
                  selectedSatellite.wind ? (
                    <div className="space-y-2">
                      {/* Speed row — km/h + knots */}
                      <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                        <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg shadow-sm">💨</div>
                        <div>
                          <div className="text-xs text-slate-500">{t("windSpeed")}</div>
                          <div className="text-sm font-semibold text-slate-800">
                            {selectedSatellite.wind.wind_speed_kmh} km/h
                            <span className="ml-2 text-xs text-slate-400 font-normal">
                              ({selectedSatellite.wind.wind_speed_knots} kn)
                            </span>
                          </div>
                        </div>
                      </div>
                      {/* Direction */}
                      <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                        <WindDirectionArrow direction={selectedSatellite.wind.wind_direction} />
                        <div>
                          <div className="text-xs text-slate-500">{t("windDirection")}</div>
                          <div className="text-sm font-semibold text-slate-800">
                            {selectedSatellite.wind.wind_direction}° {getCardinalDirection(selectedSatellite.wind.wind_direction)}
                          </div>
                        </div>
                      </div>
                      {/* Timestamp */}
                      {selectedSatellite.wind.timestamp && (
                        <div className="text-xs text-slate-400 text-right pt-1">
                          🕐 {new Date(selectedSatellite.wind.timestamp).toUTCString().slice(0, 25)} UTC
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="py-4 text-center text-slate-500 text-sm">{t("noWindData")}</div>
                  )

                ) : satelliteTab === "wave" ? (
                  selectedSatellite.wave ? (
                    <div className="space-y-2">
                      {/* Significant wave height */}
                      <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                        <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg shadow-sm">🌊</div>
                        <div>
                          <div className="text-xs text-slate-500">{t("waveHeight")}</div>
                          <div className="text-sm font-semibold text-slate-800">
                            {selectedSatellite.wave.significant_wave_height_m} m
                          </div>
                        </div>
                      </div>
                      {/* Period */}
                      {selectedSatellite.wave.mean_wave_period && (
                        <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                          <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg shadow-sm">⏱️</div>
                          <div>
                            <div className="text-xs text-slate-500">{t("wavePeriod")}</div>
                            <div className="text-sm font-semibold text-slate-800">
                              {selectedSatellite.wave.mean_wave_period} s
                            </div>
                          </div>
                        </div>
                      )}
                      {/* Direction */}
                      {selectedSatellite.wave.mean_wave_direction && (
                        <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                          <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg shadow-sm">🧭</div>
                          <div>
                            <div className="text-xs text-slate-500">{t("waveDirection")}</div>
                            <div className="text-sm font-semibold text-slate-800">
                              {selectedSatellite.wave.mean_wave_direction}° {getCardinalDirection(selectedSatellite.wave.mean_wave_direction)}
                            </div>
                          </div>
                        </div>
                      )}
                      {selectedSatellite.wave.timestamp && (
                        <div className="text-xs text-slate-400 text-right pt-1">
                          🕐 {new Date(selectedSatellite.wave.timestamp).toUTCString().slice(0, 25)} UTC
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="py-4 text-center text-slate-500 text-sm">{t("noWaveData")}</div>
                  )

                ) : satelliteTab === "current" ? (
                  /* ── Currents tab ─────────────────────────────────── */
                  selectedSatellite.current ? (
                    <div className="space-y-2">
                      {/* Speed */}
                      <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                        <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg shadow-sm">⚡</div>
                        <div>
                          <div className="text-xs text-slate-500">{t("currentSurfaceSpeed")}</div>
                          <div className="text-sm font-semibold text-slate-800">
                            {selectedSatellite.current.speed_knots} kn
                            <span className="ml-2 text-xs text-slate-400 font-normal">
                              ({selectedSatellite.current.speed_kmh} km/h)
                            </span>
                          </div>
                        </div>
                      </div>
                      {/* Direction */}
                      <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
                        <div className="w-8 h-8 bg-white rounded-md flex items-center justify-center text-lg shadow-sm">🧭</div>
                        <div>
                          <div className="text-xs text-slate-500">{t("currentDirection")}</div>
                          <div className="text-sm font-semibold text-slate-800">
                            {selectedSatellite.current.direction_deg}° {getCardinalDirection(selectedSatellite.current.direction_deg)}
                          </div>
                        </div>
                      </div>
                      {/* U/V components */}
                      <div className="flex gap-2">
                        <div className="flex-1 p-2.5 bg-slate-50 rounded-lg">
                          <div className="text-xs text-slate-500">{t("currentEast")}</div>
                          <div className="text-sm font-semibold text-slate-800">{selectedSatellite.current.u_component} m/s</div>
                        </div>
                        <div className="flex-1 p-2.5 bg-slate-50 rounded-lg">
                          <div className="text-xs text-slate-500">{t("currentNorth")}</div>
                          <div className="text-sm font-semibold text-slate-800">{selectedSatellite.current.v_component} m/s</div>
                        </div>
                      </div>
                      {selectedSatellite.current.timestamp && (
                        <div className="text-xs text-slate-400 text-right pt-1">
                          🕐 {new Date(selectedSatellite.current.timestamp).toUTCString().slice(0, 25)} UTC
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="py-4 text-center text-slate-500 text-sm">{t("noCurrentData")}</div>
                  )

                ) : satelliteTab === "point" ? (
                  /* ── Point Info tab ───────────────────────────────── */
                  <div className="space-y-3">
                    {/* Name field */}
                    <div>
                      <label className="block text-xs font-semibold text-slate-600 mb-1">{t("waypointName")}</label>
                      <input
                        type="text"
                        value={pointInfoName}
                        onChange={(e) => setPointInfoName(e.target.value)}
                        placeholder={t("waypointNamePlaceholder")}
                        className="w-full text-sm border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-400"
                      />
                    </div>

                    {/* Flag slots */}
                    {[0, 1].map((idx) => (
                      <div key={idx}>
                        <label className="block text-xs font-semibold text-slate-600 mb-1">
                          {t("flag")} {idx + 1}
                        </label>
                        {pointInfoFlags[idx] ? (
                          <div className="flex items-center gap-2">
                            <img
                              src={pointInfoFlags[idx]}
                              alt={`flag-${idx + 1}`}
                              className="h-8 w-12 object-cover rounded border border-slate-200"
                            />
                            <button
                              onClick={() =>
                                setPointInfoFlags((f) => {
                                  const n = [...f]; n[idx] = null; return n;
                                })
                              }
                              className="text-xs text-red-500 hover:text-red-700 font-medium transition-colors"
                            >
                              {t("removeFlag")}
                            </button>
                          </div>
                        ) : (
                          <label className="flex items-center gap-2 cursor-pointer bg-slate-50
                            border border-dashed border-slate-300 rounded-lg px-3 py-2 text-xs
                            text-slate-500 hover:bg-slate-100 transition-colors">
                            <span>{t("uploadFlag")}</span>
                            <input
                              type="file"
                              accept="image/*"
                              className="hidden"
                              onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (!file) return;
                                const reader = new FileReader();
                                reader.onload = (ev) => {
                                  setPointInfoFlags((f) => {
                                    const n = [...f]; n[idx] = ev.target.result; return n;
                                  });
                                };
                                reader.readAsDataURL(file);
                                e.target.value = "";
                              }}
                            />
                          </label>
                        )}
                      </div>
                    ))}

                    {/* Save & Close */}
                    <button
                      onClick={handleSaveDrawPointMeta}
                      className="w-full py-2 bg-violet-600 hover:bg-violet-700 text-white
                        text-sm font-semibold rounded-lg transition-colors"
                    >
                      {t("saveClose")}
                    </button>
                  </div>

                ) : null}
              </div>
            </div>
          </Popup>
        )}

        {/* ── Catamaran simulation marker ────────────────────────────────── */}
        {simulationMode && (
          <CatamaranMarker
            latitude={legContext ? legContext.snappedPosition[1] : activeCatamaranPos.lat}
            longitude={legContext ? legContext.snappedPosition[0] : activeCatamaranPos.lon}
            bearing={legContext?.bearing ?? 0}
            onDragEnd={handleCatamaranDrag}
          />
        )}

        {/* Escales obligatoires — drapeaux toujours visibles, tooltip au survol (hidden during drawing) */}
        {!drawingMode && points.map((p, i) =>
          p.flag !== "" ? (
            <Marker key={i} longitude={p.lon} latitude={p.lat} anchor="bottom" offset={markerOffsets[i]}>
              <div
                onMouseEnter={() => setHoveredPoint(i)}
                onMouseLeave={() => setHoveredPoint(null)}
                style={{ position: "relative", cursor: "default" }}
              >
                <img
                  src={p.flag}
                  alt={p.name}
                  style={{
                    width: 36,
                    height: 26,
                    borderRadius: 4,
                    boxShadow: "0 2px 8px rgba(0,0,0,0.45)",
                    display: "block",
                  }}
                />
                {/* Tooltip — nom de l'escale au survol */}
                {hoveredPoint === i && (
                  <div
                    style={{
                      position: "absolute",
                      bottom: "calc(100% + 7px)",
                      left: "50%",
                      transform: "translateX(-50%)",
                      background: "rgba(15,23,42,0.95)",
                      color: "#fff",
                      fontSize: "11px",
                      fontWeight: 600,
                      padding: "4px 9px",
                      borderRadius: "6px",
                      whiteSpace: "nowrap",
                      pointerEvents: "none",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.35)",
                      letterSpacing: "0.02em",
                    }}
                  >
                    {p.name}
                  </div>
                )}
              </div>
            </Marker>
          ) : (
            /* Waypoints intermédiaires — point bleu toujours visible, tooltip au survol */
            <Marker key={i} longitude={p.lon} latitude={p.lat} offset={markerOffsets[i]}>
              <div
                onMouseEnter={() => setHoveredPoint(i)}
                onMouseLeave={() => setHoveredPoint(null)}
                style={{ position: "relative", cursor: "default" }}
              >
                <div
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    backgroundColor: "#0077ff",
                    border: "2px solid white",
                    boxShadow: "0 0 4px rgba(0,119,255,0.6)",
                  }}
                />
                {hoveredPoint === i && (
                  <div
                    style={{
                      position: "absolute",
                      bottom: "calc(100% + 7px)",
                      left: "50%",
                      transform: "translateX(-50%)",
                      background: "rgba(15,23,42,0.95)",
                      color: "#fff",
                      fontSize: "11px",
                      fontWeight: 600,
                      padding: "4px 9px",
                      borderRadius: "6px",
                      whiteSpace: "nowrap",
                      pointerEvents: "none",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.35)",
                      letterSpacing: "0.02em",
                    }}
                  >
                    {p.name}
                  </div>
                )}
              </div>
            </Marker>
          )
        )}

        {/* Balisage — raster AU-DESSUS de tout (routes, markers) pour être visible */}
        <BalisageLayer show={maritimeLayers.showBalisage} />
      </Map>
    </div>
  );
}

