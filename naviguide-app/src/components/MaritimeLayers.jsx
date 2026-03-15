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
import { useLang } from "../i18n/LangContext.jsx";

// Toujours URL absolue pour les tuiles (évite les problèmes de proxy Vite / preview).
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
const EMPTY_FC = { type: "FeatureCollection", features: [] };

// ── Layer paint styles ────────────────────────────────────────────────────────

// ZEE via WMS — layer eez_boundaries = limites uniquement (polylignes, pas de polygones)
// Tuiles 512×512 pour un rendu plus fin au zoom minimal (moins de flou/épaisseur)
const ZEE_WMS_TILES = [
  `${API_BASE}/proxy/zee/wms?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=eez_boundaries&FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:3857&WIDTH=512&HEIGHT=512&BBOX={bbox-epsg-3857}`,
];
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

async function fetchPorts() {
  const url = `${API_BASE}/proxy/ports`;
  const res = await fetch(url);
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

  const [portsData, setPortsData] = useState(EMPTY_FC);

  const [loadingPorts, setLoadingPorts] = useState(false);

  const [errorPorts, setErrorPorts] = useState(null);

  // Chargement Ports — immédiat
  useEffect(() => {
    if (!showPorts || portsData.features.length > 0) return;
    setLoadingPorts(true);
    setErrorPorts(null);
    fetchPorts()
      .then((data) => { setPortsData(data); })
      .catch((e) => { console.warn("[MaritimeLayers] Ports:", e.message || e); setErrorPorts(e.message || String(e)); })
      .finally(() => setLoadingPorts(false));
  }, [showPorts]);

  return {
    // Toggles
    showZee,      setShowZee,
    showPorts,    setShowPorts,
    showBalisage, setShowBalisage,
    // Data
    portsData,
    // Loading flags
    loadingZee: false,   // ZEE WMS = tuiles, pas de fetch
    loadingPorts,
    loadingBalisage: false,
    // Error messages
    errorZee: null,
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
  showZee,
  showPorts, portsData,
  showBalisage,
}) {
  const vis = (flag) => ({ visibility: flag ? "visible" : "none" });

  return (
    <>
      {/* ── ZEE via WMS (tuiles à la demande, instantané) ────────────────── */}
      <Source
        id="zee-source"
        type="raster"
        tiles={ZEE_WMS_TILES}
        tileSize={512}
        minzoom={1}
        maxzoom={18}
      >
        <Layer
          id="zee-layer"
          type="raster"
          layout={vis(showZee)}
          paint={{
            "raster-opacity": ["interpolate", ["linear"], ["zoom"], 1, 0.25, 3, 0.45, 6, 0.7, 10, 0.9],
            "raster-fade-duration": 0,
            "raster-resampling": "nearest",
          }}
        />
      </Source>

      {/* ── WPI ports circles ───────────────────────────────────────────── */}
      <Source id="ports-source" type="geojson" data={portsData}>
        <Layer id="ports-circle" type="circle" layout={vis(showPorts)} paint={PORTS_CIRCLE_PAINT} />
      </Source>
    </>
  );
}

/** Balisage — raster au-dessus de tout (routes, markers). À placer EN DERNIER dans <Map>. */
const SEAMARK_TILES = [
  `${API_BASE}/proxy/seamark/{z}/{x}/{y}.png`,
  "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
];

export function BalisageLayer({ show }) {
  return (
    <Source
      id="openseamap-source"
      type="raster"
      tiles={SEAMARK_TILES}
      tileSize={256}
      minzoom={1}
      maxzoom={19}
      attribution="© OpenSeaMap"
    >
      <Layer
        id="openseamap-layer"
        type="raster"
        layout={{ visibility: show ? "visible" : "none" }}
        paint={{
          "raster-opacity": 1,
          "raster-fade-duration": 0,
        }}
      />
    </Source>
  );
}

// ── Toggle panel (render outside <Map>) ──────────────────────────────────────

const LAYER_CONFIG = [
  { key: "zee",      labelKey: "layerZee",      titleKey: "layerZeeTitle",      color: "#0e7490", showKey: "showZee",      toggleKey: "setShowZee",      loadingKey: "loadingZee",      errorKey: "errorZee" },
  { key: "ports",    labelKey: "layerPorts",    titleKey: "layerPortsTitle",    color: "#f59e0b", showKey: "showPorts",    toggleKey: "setShowPorts",    loadingKey: "loadingPorts",    errorKey: "errorPorts" },
  { key: "balisage", labelKey: "layerBalisage", titleKey: "layerBalisageTitle", color: "#10b981", showKey: "showBalisage", toggleKey: "setShowBalisage", loadingKey: "loadingBalisage", errorKey: "errorBalisage" },
];

/**
 * MaritimeLayersPanel
 * Panneau flottant avec les boutons de bascule pour chaque couche maritime.
 * À placer EN DEHORS du composant <Map>, dans le div racine de l'application.
 */
export function MaritimeLayersPanel(props) {
  const { t } = useLang();
  return (
    /* Centré en bas, entre les deux sidebars (chacune 320px) — toujours visible */
    <div
      className="absolute bottom-5 left-1/2 -translate-x-1/2 z-25 flex flex-row items-center gap-1.5
                 bg-slate-900/80 backdrop-blur-sm border border-white/10 rounded-full px-3 py-1.5 shadow-xl"
      style={{ pointerEvents: "auto", zIndex: 25 }}
    >
      {/* Label */}
      <span className="text-white/35 text-[9px] font-semibold uppercase tracking-widest mr-1 select-none">
        {t("layersLabel")}
      </span>

      {LAYER_CONFIG.map(({ key, labelKey, titleKey, color, showKey, toggleKey, loadingKey, errorKey }) => {
        const active  = props[showKey];
        const loading = props[loadingKey];
        const error   = props[errorKey];

        return (
          <button
            key={key}
            onClick={() => props[toggleKey]((v) => !v)}
            title={t(titleKey)}
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
            <span>{t(labelKey)}</span>
            {error && !loading && (
              <span className="text-red-400 text-[10px]" title={error}>⚠</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
