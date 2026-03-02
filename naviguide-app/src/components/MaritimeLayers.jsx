/**
 * MaritimeLayers — 3 couches de données maritimes pour MapLibre GL JS
 *
 *  1. ZEE         — Zones Économiques Exclusives (VLIZ / Marine Regions, via WFS proxy)
 *  2. Ports WPI   — World Port Index (NGA/MSI REST, via proxy, coords DMS→decimal)
 *  3. Balisage    — Balisage maritime via OpenSeaMap raster tiles (public, no auth)
 *                   NOTE: SHOM WFS remplacé car nécessite authentification (401).
 *
 * Exports:
 *  - useMaritimeLayers()        → hook (state + data fetching)
 *  - MaritimeLayers(props)      → Sources/Layers à placer DANS <Map>
 *  - MaritimeLayersPanel(props) → Panneau flottant de bascule (HORS <Map>)
 */

import { useEffect, useState } from "react";
import { Source, Layer } from "react-map-gl/maplibre";

const API_URL = import.meta.env.VITE_API_URL;
const EMPTY_FC = { type: "FeatureCollection", features: [] };

// ── Layer paint styles ────────────────────────────────────────────────────────

const ZEE_FILL_PAINT = {
  "fill-color": "rgba(14, 116, 144, 0.07)",
  "fill-outline-color": "rgba(14, 116, 144, 0)",
};
const ZEE_LINE_PAINT = {
  "line-color": "#0e7490",
  "line-width": 1.5,
  "line-dasharray": [5, 3],
  "line-opacity": 0.8,
};
const PORTS_CIRCLE_PAINT = {
  "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 2, 6, 4, 10, 7],
  "circle-color": "#f59e0b",
  "circle-stroke-width": 1,
  "circle-stroke-color": "#fff",
  "circle-opacity": 0.85,
};
// OpenSeaMap tiles — raster overlay, opacity controlled via show flag
const OPENSEAMAP_RASTER_PAINT = {
  "raster-opacity": 0.85,
};

// ── Fetchers ──────────────────────────────────────────────────────────────────

async function fetchZee() {
  const res = await fetch(`${API_URL}/proxy/zee?maxFeatures=200`);
  if (!res.ok) throw new Error(`ZEE HTTP ${res.status}`);
  return res.json();
}

async function fetchPorts() {
  const res = await fetch(`${API_URL}/proxy/ports`);
  if (!res.ok) throw new Error(`Ports HTTP ${res.status}`);
  return res.json();
}

// ── Hook ──────────────────────────────────────────────────────────────────────

/**
 * useMaritimeLayers
 * Gère l'état ON/OFF, les données GeoJSON et les états de chargement
 * pour les 3 couches maritimes.
 */
export function useMaritimeLayers() {
  // Couches actives par défaut — chargement différé pour ne pas bloquer le rendu initial
  const [showZee,      setShowZee]      = useState(true);
  const [showPorts,    setShowPorts]    = useState(true);
  const [showBalisage, setShowBalisage] = useState(true);

  const [zeeData,   setZeeData]   = useState(EMPTY_FC);
  const [portsData, setPortsData] = useState(EMPTY_FC);

  const [loadingZee,   setLoadingZee]   = useState(false);
  const [loadingPorts, setLoadingPorts] = useState(false);

  const [errorZee,   setErrorZee]   = useState(null);
  const [errorPorts, setErrorPorts] = useState(null);

  // Lazy-load ZEE — différé de 3 s pour ne pas concurrencer le chargement des routes
  useEffect(() => {
    if (!showZee || zeeData.features.length > 0) return;
    const t = setTimeout(() => {
      setLoadingZee(true);
      setErrorZee(null);
      fetchZee()
        .then(setZeeData)
        .catch((e) => { console.warn("[MaritimeLayers] ZEE:", e); setErrorZee(e.message); })
        .finally(() => setLoadingZee(false));
    }, 3000);
    return () => clearTimeout(t);
  }, [showZee]);

  // Lazy-load WPI ports — différé de 5 s (staggeré après ZEE)
  useEffect(() => {
    if (!showPorts || portsData.features.length > 0) return;
    const t = setTimeout(() => {
      setLoadingPorts(true);
      setErrorPorts(null);
      fetchPorts()
        .then(setPortsData)
        .catch((e) => { console.warn("[MaritimeLayers] Ports:", e); setErrorPorts(e.message); })
        .finally(() => setLoadingPorts(false));
    }, 5000);
    return () => clearTimeout(t);
  }, [showPorts]);

  return {
    // Toggles
    showZee,      setShowZee,
    showPorts,    setShowPorts,
    showBalisage, setShowBalisage,
    // Data
    zeeData,
    portsData,
    // Loading flags
    loadingZee,
    loadingPorts,
    loadingBalisage: false,   // OpenSeaMap tiles load automatically
    // Error messages
    errorZee,
    errorPorts,
    errorBalisage: null,
  };
}

// ── Map layers (render inside <Map>) ─────────────────────────────────────────

/**
 * MaritimeLayers
 * Place les Sources/Layers MapLibre GL JS dans l'arbre du composant <Map>.
 *
 * IMPORTANT: toutes les sources sont TOUJOURS montées (pas de rendu conditionnel).
 * La visibilité est contrôlée via layout.visibility pour éviter les erreurs
 * MapLibre au mount/unmount des sources ("Source already exists", race conditions).
 *
 *  - ZEE       : polygones GeoJSON via proxy backend
 *  - Ports WPI : points GeoJSON via proxy backend
 *  - Balisage  : tuiles raster OpenSeaMap (chargées directement depuis le navigateur)
 */
export function MaritimeLayers({
  showZee, zeeData,
  showPorts, portsData,
  showBalisage,
}) {
  const vis = (flag) => ({ visibility: flag ? "visible" : "none" });

  return (
    <>
      {/* ── ZEE polygons — rendu SOUS la route bleue (beforeId) ─────────── */}
      <Source id="zee-source" type="geojson" data={zeeData}>
        <Layer id="zee-fill" type="fill"  beforeId="maritime-layer" layout={vis(showZee)} paint={ZEE_FILL_PAINT} />
        <Layer id="zee-line" type="line"  beforeId="maritime-layer" layout={vis(showZee)} paint={ZEE_LINE_PAINT} />
      </Source>

      {/* ── WPI ports circles — rendu SOUS la route bleue ────────────────── */}
      <Source id="ports-source" type="geojson" data={portsData}>
        <Layer id="ports-circle" type="circle" beforeId="maritime-layer" layout={vis(showPorts)} paint={PORTS_CIRCLE_PAINT} />
      </Source>

      {/* ── OpenSeaMap balisage — raster tile overlay SOUS la route bleue ── */}
      {/* NOTE: raster-opacity (paint) plutôt que layout.visibility car MapLibre
          ne charge pas les tuiles des layers "none". */}
      <Source
        id="openseamap-source"
        type="raster"
        tiles={["https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png"]}
        tileSize={256}
        attribution="© <a href='https://www.openseamap.org' target='_blank'>OpenSeaMap</a>"
      >
        <Layer
          id="openseamap-layer"
          type="raster"
          beforeId="maritime-layer"
          paint={{ "raster-opacity": showBalisage ? 0.85 : 0 }}
        />
      </Source>
    </>
  );
}

// ── Toggle panel (render outside <Map>) ──────────────────────────────────────

const LAYER_CONFIG = [
  {
    key: "zee",
    label: "ZEE",
    title: "Zones Économiques Exclusives (VLIZ)",
    color: "#0e7490",
    showKey: "showZee",
    toggleKey: "setShowZee",
    loadingKey: "loadingZee",
    errorKey: "errorZee",
  },
  {
    key: "ports",
    label: "Ports WPI",
    title: "Ports mondiaux — World Port Index (NGA)",
    color: "#f59e0b",
    showKey: "showPorts",
    toggleKey: "setShowPorts",
    loadingKey: "loadingPorts",
    errorKey: "errorPorts",
  },
  {
    key: "balisage",
    label: "Balisage",
    title: "Balisage maritime (OpenSeaMap)",
    color: "#10b981",
    showKey: "showBalisage",
    toggleKey: "setShowBalisage",
    loadingKey: "loadingBalisage",
    errorKey: "errorBalisage",
  },
];

/**
 * MaritimeLayersPanel
 * Panneau flottant avec les boutons de bascule pour chaque couche maritime.
 * À placer EN DEHORS du composant <Map>, dans le div racine de l'application.
 */
export function MaritimeLayersPanel(props) {
  return (
    /* Centré en bas, entre les deux sidebars (chacune 320px) — toujours visible */
    <div
      className="absolute bottom-5 left-1/2 -translate-x-1/2 z-25 flex flex-row items-center gap-1.5
                 bg-slate-900/80 backdrop-blur-sm border border-white/10 rounded-full px-3 py-1.5 shadow-xl"
      style={{ pointerEvents: "auto", zIndex: 25 }}
    >
      {/* Label */}
      <span className="text-white/35 text-[9px] font-semibold uppercase tracking-widest mr-1 select-none">
        Couches
      </span>

      {LAYER_CONFIG.map(({ key, label, title, color, showKey, toggleKey, loadingKey, errorKey }) => {
        const active  = props[showKey];
        const loading = props[loadingKey];
        const error   = props[errorKey];

        return (
          <button
            key={key}
            onClick={() => props[toggleKey]((v) => !v)}
            title={title}
            className={[
              "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold",
              "transition-all duration-150 select-none",
              active
                ? "bg-slate-700/90 text-white border border-white/20"
                : "bg-transparent text-white/45 border border-white/10 hover:text-white/80 hover:bg-slate-700/50",
              error ? "border-red-500/50" : "",
            ].join(" ")}
          >
            {loading ? (
              <div className="w-2 h-2 rounded-full border-2 border-white/30 border-t-white animate-spin flex-shrink-0" />
            ) : (
              <div
                className="w-2 h-2 rounded-full flex-shrink-0 transition-colors"
                style={{
                  backgroundColor: active ? color : "transparent",
                  border: `1.5px solid ${error ? "#ef4444" : color}`,
                }}
              />
            )}
            <span>{label}</span>
            {error && !loading && (
              <span className="text-red-400 text-[10px]" title={error}>⚠</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
