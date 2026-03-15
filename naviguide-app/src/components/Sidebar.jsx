/**
 * NAVIGUIDE v2 — Expedition Sidebar
 * Shows voyage statistics, LLM executive briefing, and critical alerts.
 * The Berry-Mappemonde card is an interactive route switcher with file import.
 */
import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, AlertTriangle, Navigation, Shield, Upload, X, Pencil, CheckCircle, Send, Loader2, Compass, Play, Square } from "lucide-react";
import { riskBadgeClass } from "../utils/riskColors";
import { useLang } from "../i18n/LangContext.jsx";
import { SimulationPanel } from "./SimulationPanel";
import { AgentPanel } from "./AgentPanel";

const POLAR_API_URL = import.meta.env.VITE_POLAR_API_URL ?? "http://localhost:8004";

/* ── Polar Chat bubble ───────────────────────────────────────────────────── */
function PolarChatBubble({ role, content }) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-1.5`}>
      <div className={`max-w-[88%] px-3 py-2 rounded-xl text-xs leading-relaxed whitespace-pre-wrap
        ${isUser
          ? "bg-blue-600 text-white rounded-br-none"
          : "bg-slate-700/80 text-slate-200 rounded-bl-none border border-slate-600/40"
        }`}>
        {content}
      </div>
    </div>
  );
}

/* ── Polar Chat section (rendered inside Sidebar above briefing) ────────── */
function PolarChatSection({ polarData }) {
  const { t } = useLang();
  const [messages,    setMessages]    = useState([]);
  const [chatInput,   setChatInput]   = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const chatEndRef  = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-resize textarea as content grows/shrinks
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 112) + "px";
  }, [chatInput]);

  const handleSend = async () => {
    const msg = chatInput.trim();
    if (!msg || chatLoading || !polarData?.expedition_id) return;
    const userMsg     = { role: "user", content: msg };
    const nextHistory = [...messages, userMsg];
    setMessages(nextHistory);
    setChatInput("");
    setChatLoading(true);
    try {
      const res  = await fetch(`${POLAR_API_URL}/api/v1/polar/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          expedition_id: polarData.expedition_id,
          message:       msg,
          history:       messages.slice(-6),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
      setMessages([...nextHistory, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setMessages([...nextHistory, { role: "assistant", content: `⚠️ ${err.message}` }]);
    } finally {
      setChatLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  return (
    <div>
      <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
        <Compass size={12} className="text-blue-400" />
        Chat
      </div>

      {/* Messages */}
      <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
        <div className="max-h-48 overflow-y-auto sidebar-scroll px-3 py-3 space-y-0.5">
          {messages.length === 0 && !polarData && (
            <p className="text-xs text-slate-500 text-center py-3">
              {t("polarChatLoadPrompt")}
            </p>
          )}
          {messages.map((m, i) => <PolarChatBubble key={i} role={m.role} content={m.content} />)}
          {chatLoading && (
            <div className="flex justify-start mb-1.5">
              <div className="bg-slate-700/60 border border-slate-600/40 px-3 py-2 rounded-xl rounded-bl-none">
                <Loader2 size={11} className="animate-spin text-blue-400" />
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div className="border-t border-slate-700/50 px-3 py-2 flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={polarData ? t("polarChatAskPlaceholder") : t("polarChatLoadFirst")}
            disabled={!polarData}
            rows={1}
            className="flex-1 bg-transparent text-xs text-white placeholder-slate-500 resize-none
              focus:outline-none leading-relaxed disabled:opacity-40 overflow-y-auto"
            style={{ maxHeight: "112px" }}
          />
          <button
            onClick={handleSend}
            disabled={!chatInput.trim() || chatLoading || !polarData}
            className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-all
              ${(!chatInput.trim() || chatLoading || !polarData)
                ? "text-slate-600 cursor-not-allowed"
                : "bg-blue-600 hover:bg-blue-500 text-white active:scale-95"}`}
          >
            <Send size={12} />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Logo image paths (served from /public) ──────────────────────────────── */
const NAVIGUIDE_LOGO = "/logo-naviguide-7479b7aa.png";
const BERRY_LOGO     = "/logo-berry-mappemonde.png";

/* ── File parsers ─────────────────────────────────────────────────────────── */

/** Parse a GeoJSON string → FeatureCollection preserving one Feature per LineString */
function parseGeoJSON(text) {
  const data     = JSON.parse(text);
  const features = [];

  const extract = (geometry, props = {}) => {
    if (!geometry) return;
    switch (geometry.type) {
      case "LineString":
        features.push({
          type: "Feature",
          properties: props,
          geometry: { type: "LineString", coordinates: geometry.coordinates.map(([lon, lat]) => [lon, lat]) },
        });
        break;
      case "MultiLineString":
        geometry.coordinates.forEach((line) =>
          features.push({
            type: "Feature",
            properties: props,
            geometry: { type: "LineString", coordinates: line.map(([lon, lat]) => [lon, lat]) },
          })
        );
        break;
      case "GeometryCollection":
        geometry.geometries.forEach((g) => extract(g, props));
        break;
      default:
        break;
    }
  };

  if (data.type === "FeatureCollection") {
    data.features.forEach((f) => extract(f.geometry, f.properties || {}));
  } else if (data.type === "Feature") {
    extract(data.geometry, data.properties || {});
  } else {
    extract(data);
  }

  return { type: "FeatureCollection", features };
}

/** Parse a KML string → FeatureCollection with one Feature per Placemark LineString */
function parseKML(text) {
  const doc      = new DOMParser().parseFromString(text, "application/xml");
  const features = [];

  // Each <Placemark> that contains a <LineString> becomes one Feature
  doc.querySelectorAll("Placemark").forEach((placemark) => {
    const nameEl = placemark.querySelector("name");
    const name   = nameEl?.textContent?.trim() || "";

    placemark.querySelectorAll("LineString coordinates").forEach((el) => {
      const coords = [];
      el.textContent.trim().split(/\s+/).forEach((pt) => {
        const [lonStr, latStr] = pt.split(",");
        const lon = parseFloat(lonStr);
        const lat = parseFloat(latStr);
        if (!isNaN(lon) && !isNaN(lat)) coords.push([lon, lat]);
      });
      if (coords.length > 0) {
        features.push({
          type: "Feature",
          properties: { name },
          geometry: { type: "LineString", coordinates: coords },
        });
      }
    });
  });

  return { type: "FeatureCollection", features };
}

/** Strip directory path and extension from a filename */
function stemName(filename) {
  return filename
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.(geojson|kml|json)$/i, "");
}

/* ── Sub-components ───────────────────────────────────────────────────────── */

function StatCard({ icon, label, value, sub }) {
  return (
    <div className="bg-slate-800/70 rounded-xl p-3 flex items-start gap-3">
      <div className="mt-0.5 text-slate-400">{icon}</div>
      <div className="min-w-0">
        <div className="text-xs text-slate-500 mb-0.5">{label}</div>
        <div className="text-sm font-semibold text-white truncate">{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}

function RiskBadge({ level }) {
  const cls = riskBadgeClass[level] || riskBadgeClass.UNKNOWN;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ${cls}`}>
      {level || "UNKNOWN"}
    </span>
  );
}

function AlertItem({ alert }) {
  const { t } = useLang();
  const colors = {
    CRITICAL: "border-red-700/60 bg-red-950/40",
    HIGH:     "border-orange-700/60 bg-orange-950/40",
  };
  const cls = colors[alert.risk_level] || "border-slate-700 bg-slate-800/40";
  return (
    <div className={`border rounded-lg p-2.5 ${cls}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-white truncate">{alert.waypoint}</span>
        <RiskBadge level={alert.risk_level} />
      </div>
      <div className="text-xs text-slate-400 mt-1 capitalize">
        {t("dominantRisk")}: {alert.dominant_risk?.replace("_score", "") || "—"}
      </div>
    </div>
  );
}

/* ── BerryCard ────────────────────────────────────────────────────────────── */
/**
 * States:
 *  "berry-active"            – Berry highlighted (default). Click → "import-mode".
 *  "import-mode"             – Shows two import buttons. Berry route still shown.
 *  "file-active"             – Imported file is the active route. Shows Berry mini-btn + filename (highlighted).
 *  "berry-active-file-loaded"– Berry is active route, file is in memory. Shows Berry (highlighted) + filename.
 */
function BerryCard({ onRouteImport, onRouteSwitchToBerry, isDrawing, onDrawStart, onDrawFinish }) {
  const { t } = useLang();
  const [cardMode, setCardMode]         = useState("berry-active");
  const [importedGeoJSON, setImportedGeoJSON] = useState(null); // FeatureCollection
  const [importedName, setImportedName]       = useState(null);
  const [importError, setImportError]         = useState(null);

  const geoJsonRef = useRef(null);
  const kmlRef     = useRef(null);

  /* ── File processing ──────────────────────────────────────────────────── */
  const processFile = (file) => {
    setImportError(null);
    const name = stemName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text    = e.target.result;
        const isKml   = file.name.toLowerCase().endsWith(".kml");
        const geojson = isKml ? parseKML(text) : parseGeoJSON(text);

        if (geojson.features.length === 0) throw new Error(t("noCoordsFound"));

        setImportedGeoJSON(geojson);
        setImportedName(name);
        setCardMode("file-active");
        onRouteImport(geojson);
      } catch (err) {
        setImportError(err.message);
      }
    };
    reader.readAsText(file);
  };

  /* ── Handlers ────────────────────────────────────────────────────────── */
  const handleCardClick = () => {
    if (cardMode === "berry-active" || cardMode === "berry-active-file-loaded") {
      setCardMode("import-mode");
    }
  };

  const handleImportFile = (ref) => ref.current?.click();

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (file) processFile(file);
    e.target.value = ""; // allow re-import of same file
  };

  const handleBerryMiniClick = (e) => {
    e.stopPropagation();
    setCardMode("berry-active-file-loaded");
    onRouteSwitchToBerry();
  };

  const handleFileNameClick = (e) => {
    e.stopPropagation();
    if (importedGeoJSON) {
      setCardMode("file-active");
      onRouteImport(importedGeoJSON);
    }
  };

  const handleCancelImport = (e) => {
    e.stopPropagation();
    // Return to appropriate mode without importing
    setCardMode(importedGeoJSON ? "berry-active-file-loaded" : "berry-active");
  };

  const handleFinishDrawing = () => {
    const geojson = onDrawFinish(); // App stops drawing and returns built FeatureCollection
    if (geojson?.features?.length > 0) {
      setImportedGeoJSON(geojson);
      setImportedName(t("customRoute"));
      setCardMode("file-active");
      onRouteImport(geojson);
    } else {
      // Nothing drawn yet — just exit drawing mode
      setCardMode(importedGeoJSON ? "berry-active-file-loaded" : "berry-active");
    }
  };

  /* ── Render helpers ──────────────────────────────────────────────────── */

  // Glow ring for active state
  const glowCls   = "border-blue-500/70 bg-blue-950/30 shadow-[0_0_12px_2px_rgba(59,130,246,0.25)]";
  const normalCls = "border-slate-700/50 bg-slate-800/60";

  /* State: berry-active */
  if (cardMode === "berry-active") {
    return (
      <button
        onClick={handleCardClick}
        title={t("clickToImport")}
        className={`w-full flex items-center gap-3 rounded-xl px-3 py-2 border
          transition-all duration-200 hover:border-blue-400/50 cursor-pointer ${glowCls}`}
      >
        <img src={BERRY_LOGO} alt="Berry-Mappemonde"
          className="h-12 w-auto object-contain rounded-lg flex-shrink-0" style={{ maxWidth: 90 }} />
        <div className="text-white font-bold text-sm leading-tight tracking-wide">BERRY-MAPPEMONDE</div>
      </button>
    );
  }

  /* State: import-mode */
  if (cardMode === "import-mode") {
    return (
      <div className={`rounded-xl px-3 py-2 border ${normalCls}`}>
        {/* Header row */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-slate-400 font-medium">{t("importOrDraw")}</span>
          <button onClick={handleCancelImport}
            className="text-slate-500 hover:text-slate-300 transition-colors" title={t("cancel")}>
            <X size={13} />
          </button>
        </div>

        {/* Import buttons */}
        <div className="flex gap-2 mb-2">
          <button
            onClick={() => handleImportFile(geoJsonRef)}
            className="flex-1 flex items-center justify-center gap-1.5 bg-blue-600/20 hover:bg-blue-600/40
              border border-blue-500/40 rounded-lg px-2 py-2 text-xs text-blue-300 font-medium
              transition-all duration-150"
          >
            <Upload size={12} /> GeoJSON
          </button>
          <button
            onClick={() => handleImportFile(kmlRef)}
            className="flex-1 flex items-center justify-center gap-1.5 bg-teal-600/20 hover:bg-teal-600/40
              border border-teal-500/40 rounded-lg px-2 py-2 text-xs text-teal-300 font-medium
              transition-all duration-150"
          >
            <Upload size={12} /> KML
          </button>
        </div>

        {/* Draw / Finish button */}
        {isDrawing ? (
          <button
            onClick={handleFinishDrawing}
            className="w-full flex items-center justify-center gap-2 bg-green-600/30 hover:bg-green-600/50
              border border-green-500/50 rounded-lg px-2 py-2 text-xs text-green-300 font-semibold
              transition-all duration-150"
          >
            <CheckCircle size={12} /> {t("finish")}
          </button>
        ) : (
          <button
            onClick={() => onDrawStart()}
            className="w-full flex items-center justify-center gap-2 bg-violet-600/20 hover:bg-violet-600/40
              border border-violet-500/40 rounded-lg px-2 py-2 text-xs text-violet-300 font-medium
              transition-all duration-150"
          >
            <Pencil size={12} /> {t("drawOwnRoute")}
          </button>
        )}

        {importError && (
          <p className="text-xs text-red-400 mt-1.5">{importError}</p>
        )}

        {/* Hidden file inputs */}
        <input ref={geoJsonRef} type="file" accept=".geojson,.json" className="hidden"
          onChange={handleFileChange} />
        <input ref={kmlRef}     type="file" accept=".kml"           className="hidden"
          onChange={handleFileChange} />
      </div>
    );
  }

  /* State: file-active */
  if (cardMode === "file-active") {
    return (
      <div className={`rounded-xl px-3 py-2 border ${glowCls}`}>
        <div className="flex items-center gap-2">
          {/* Berry mini-button */}
          <button
            onClick={handleBerryMiniClick}
            title={t("backToBerry")}
            className="flex items-center gap-1.5 bg-slate-700/60 hover:bg-slate-600/60
              border border-slate-600/50 rounded-lg px-2 py-1 transition-all duration-150
              text-slate-400 hover:text-white flex-shrink-0"
          >
            <img src={BERRY_LOGO} alt="Berry" className="h-5 w-auto object-contain rounded" style={{ maxWidth: 28 }} />
            <span className="text-xs font-medium whitespace-nowrap">{t("berry")}</span>
          </button>

          {/* Active filename — highlighted */}
          <div className="flex-1 min-w-0 flex items-center gap-1.5">
            <span className="text-xs font-semibold text-blue-300 truncate" title={importedName}>
              {importedName}
            </span>
          </div>
        </div>
      </div>
    );
  }

  /* State: berry-active-file-loaded */
  if (cardMode === "berry-active-file-loaded") {
    return (
      <div className={`rounded-xl px-3 py-2 border ${glowCls}`}>
        <div className="flex items-center gap-2">
          {/* Berry active section — click to enter import-mode */}
          <button
            onClick={(e) => { e.stopPropagation(); setCardMode("import-mode"); }}
            title={t("clickNewImport")}
            className="flex items-center gap-2 flex-shrink-0 hover:opacity-80 transition-opacity"
          >
            <img src={BERRY_LOGO} alt="Berry-Mappemonde"
              className="h-10 w-auto object-contain rounded-lg" style={{ maxWidth: 60 }} />
            <span className="text-white font-bold text-xs leading-tight tracking-wide whitespace-nowrap">
              BERRY-MAPPEMONDE
            </span>
          </button>

          {/* Divider */}
          <div className="w-px h-8 bg-slate-600/60 flex-shrink-0" />

          {/* Imported filename — clickable to re-activate */}
          <button
            onClick={handleFileNameClick}
            title={t("showRoute", { name: importedName })}
            className="flex-1 min-w-0 text-left px-2 py-1 rounded-lg bg-slate-700/40
              hover:bg-blue-700/30 border border-slate-600/30 hover:border-blue-500/40
              transition-all duration-150"
          >
            <span className="text-xs text-slate-400 hover:text-blue-300 truncate block" title={importedName}>
              {importedName}
            </span>
          </button>
        </div>
      </div>
    );
  }

  return null;
}

/* ── Main component ───────────────────────────────────────────────────────── */

export function Sidebar({ plan, open, onToggle, onRouteImport, onRouteSwitchToBerry, isDrawing, onDrawStart, onDrawFinish, isCockpit, isOffshore, polarData, maritimeLayers, simulationMode, onSimulationToggle, legContext, onNext, canNext, onPrev, canPrev }) {
  const { t } = useLang();
  const stats    = plan?.voyage_statistics || {};
  const alerts   = plan?.critical_alerts   || [];
  const briefing = plan?.executive_briefing || "";

  return (
    <>
      {/*
        Toggle button.
        Offshore: larger (w-12 h-12, brighter border) for gloved use.
        Normal:   w-9 h-9.
      */}
      <button
        onClick={onToggle}
        className={`naviguide-sidebar-toggle absolute top-4 z-30 bg-slate-900/95 text-white
          rounded-full flex items-center justify-center shadow-lg
          hover:bg-slate-800 transition-all duration-300
          ${isOffshore
            ? "w-12 h-12 border-2 border-sky-400/70 shadow-sky-900/40"
            : "w-9 h-9 border border-slate-700"}
          ${open ? "left-[322px]" : "left-4"}`}
        title={open ? t("hideSidebar") : t("showExpeditionPanel")}
      >
        {open
          ? <ChevronLeft  size={isOffshore ? 22 : 16} />
          : <ChevronRight size={isOffshore ? 22 : 16} />}
      </button>

      {/* Sidebar panel */}
      <div
        className={`naviguide-sidebar-panel absolute top-0 left-0 h-full z-20 flex flex-col bg-slate-900/97
          shadow-2xl transition-transform duration-300
          ${isOffshore
            ? "border-r-2 border-sky-400/40"
            : "border-r border-slate-700/60"}
          ${open ? "translate-x-0" : "-translate-x-full"}`}
        style={{ width: 320 }}
      >

        {/* ── Brand header ─────────────────────────────────────────────── */}
        <div className={`px-4 ${isCockpit ? "pt-3 pb-2" : "pt-4 pb-3"} border-b border-slate-700/60 flex-shrink-0`}>

          {/*
            COCKPIT: compact horizontal header — saves vertical space so all
            data panels can be visible simultaneously without scrolling.
            ONBOARDING: centred large logo with progressive guidance feel.
          */}
          {isCockpit ? (
            <div className="flex items-center gap-2 mb-2">
              <img src={NAVIGUIDE_LOGO} alt="NAVIGUIDE"
                className="h-9 w-9 object-contain rounded-full" />
              <span className="text-white font-bold text-sm tracking-widest flex-1">NAVIGUIDE</span>
            </div>
          ) : (
            <div className="flex justify-center mb-3">
              <img
                src={NAVIGUIDE_LOGO}
                alt="NAVIGUIDE for Berry-Mappemonde"
                className="h-32 w-32 object-contain rounded-full drop-shadow-lg"
              />
            </div>
          )}

          {/* Berry-Mappemonde interactive route card */}
          <BerryCard
            onRouteImport={onRouteImport}
            onRouteSwitchToBerry={onRouteSwitchToBerry}
            isDrawing={isDrawing}
            onDrawStart={onDrawStart}
            onDrawFinish={onDrawFinish}
          />

          {/* ── Maritime layer toggles — ligne horizontale sous Berry-Mappemonde ── */}
          {maritimeLayers && (
            <div className="flex flex-col gap-1 mt-2.5">
              <div className="flex items-center gap-1">
              {[
                { key: "zee",      labelKey: "layerZee",      color: "#0e7490", showKey: "showZee",      toggleKey: "setShowZee",      loadingKey: "loadingZee",      errorKey: "errorZee" },
                { key: "ports",    labelKey: "layerPorts",    color: "#f59e0b", showKey: "showPorts",    toggleKey: "setShowPorts",    loadingKey: "loadingPorts",    errorKey: "errorPorts" },
                { key: "balisage", labelKey: "layerBalisage", color: "#10b981", showKey: "showBalisage", toggleKey: "setShowBalisage", loadingKey: "loadingBalisage", errorKey: "errorBalisage" },
              ].map(({ key, labelKey, color, showKey, toggleKey, loadingKey, errorKey }) => {
                const active  = maritimeLayers[showKey];
                const loading = maritimeLayers[loadingKey];
                const error   = maritimeLayers[errorKey];
                const label   = t(labelKey);
                const title   = error ? `${label}: ${error} — ${t("layersStartHint")}` : label;
                return (
                  <button
                    key={key}
                    onClick={() => maritimeLayers[toggleKey]((v) => !v)}
                    title={title}
                    className={[
                      "flex items-center justify-center gap-1 flex-1 px-1.5 py-1 rounded-full",
                      "text-[10px] font-semibold transition-all duration-150 select-none",
                      active
                        ? "bg-slate-700/80 text-white border border-white/10"
                        : "bg-slate-800/30 text-white/35 border border-white/5 hover:text-white/60",
                    ].join(" ")}
                  >
                    {loading
                      ? <div className="w-1.5 h-1.5 rounded-full border border-white/30 border-t-white animate-spin flex-shrink-0" />
                      : <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: active ? color : "transparent", border: `1.5px solid ${error ? "#ef4444" : color}` }} />
                    }
                    {label}
                    {error && !loading && <span className="text-red-400 text-[9px]">⚠</span>}
                  </button>
                );
              })}
              </div>
              {(maritimeLayers.errorZee || maritimeLayers.errorPorts) && (
                <div className="text-[9px] text-amber-400/90 px-2" title={t("layersApiHint")}>
                  {t("layersStartHint")}
                </div>
              )}
            </div>
          )}

          {/* ── Bouton Mode Simulation ─────────────────────────────────────── */}
          {onSimulationToggle && (
            <button
              onClick={onSimulationToggle}
              title={simulationMode ? t("exitSimulation") : t("simulationModeTooltip")}
              className={[
                "flex items-center justify-center gap-1.5 w-full mt-2 px-2 py-1.5 rounded-lg",
                "text-[10px] font-semibold transition-all duration-150 select-none border",
                simulationMode
                  ? "bg-blue-600/80 text-white border-blue-500/60 shadow-lg shadow-blue-900/30"
                  : "bg-slate-800/40 text-white/50 border-white/8 hover:text-white/80 hover:bg-slate-700/50",
              ].join(" ")}
            >
              {simulationMode
                ? <><Square size={9} className="fill-current" /><span>{t("exitSimulationShort")}</span></>
                : <><Play  size={9} className="fill-current" /><span>{t("simulationModeLabel")}</span></>
              }
            </button>
          )}
        </div>

        {/*
          ── Stats grid ───────────────────────────────────────────────────
          COCKPIT: always visible even without plan (shows dashes).
          ONBOARDING: only appears once the AI plan has loaded.
        */}
        {/* ── Scrollable content (everything below logos) ────────────────── */}
        <div className="flex-1 overflow-y-auto sidebar-scroll px-4 py-3 space-y-4">

          {/* ── Mode Simulation — panneau métriques + agents IA ─────────── */}
          {simulationMode && (
            <>
              <SimulationPanel
                legContext={legContext}
                onClose={onSimulationToggle}
                onPrev={onPrev}
                canPrev={canPrev}
                onNext={onNext}
                canNext={canNext}
              />
              <AgentPanel
                legContext={legContext}
                language={t ? (t("_lang") === "fr" ? "fr" : "en") : "fr"}
              />
            </>
          )}

        {(isCockpit || plan) && (
          <div className="pb-3 border-b border-slate-700/60">
            <div className="grid grid-cols-2 gap-2">
              <StatCard
                icon={<Navigation size={14} />}
                label={t("totalDistance")}
                value={stats.total_distance_nm ? `${stats.total_distance_nm.toLocaleString()} nm` : "—"}
                sub={`${stats.total_segments || "—"} ${t("segments")}`}
              />
              <div className="bg-slate-800/70 rounded-xl p-3 flex flex-col gap-1">
                <div className="text-xs text-slate-500">{t("expeditionRisk")}</div>
                <RiskBadge level={stats.expedition_risk_level} />
                <div className="text-xs text-slate-500 mt-0.5">
                  Score: {stats.overall_expedition_risk?.toFixed(2) ?? "—"}
                </div>
              </div>
            </div>
          </div>
        )}

          {/*
            ONBOARDING only: progressive "Getting Started" guide.
            Hidden in Cockpit — the user already knows the app.
          */}
          {!isCockpit && !plan && (
            <div className="rounded-xl border border-blue-700/30 bg-blue-950/20 p-3">
              <div className="text-xs font-semibold text-blue-300 mb-1.5 flex items-center gap-1.5">
                {t("gettingStarted")}
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                {t("gettingStartedText")}
              </p>
            </div>
          )}

          {/* Polar Chat — always shown above briefing */}
          <PolarChatSection polarData={polarData} />

          {/*
            AI Skipper Briefing.
            COCKPIT: always visible — shows placeholder when not yet loaded.
            ONBOARDING: shown only when plan data is available.
          */}
          {(isCockpit || briefing) && (
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <Shield size={12} className="text-blue-400" />
                {t("briefing")}
              </div>
              <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50">
                <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-line">
                  {briefing || t("briefingPlaceholder")}
                </p>
              </div>
            </div>
          )}

          {/*
            Critical Alerts.
            COCKPIT: always shown (with "no alerts" state for peace of mind).
            ONBOARDING: shown only when alerts exist.
          */}
          {(isCockpit || alerts.length > 0) && (
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <AlertTriangle size={12} className="text-orange-400" />
                {t("criticalAlerts")}
              </div>
              {alerts.length > 0 ? (
                <div className="space-y-2">
                  {alerts.map((alert, i) => <AlertItem key={i} alert={alert} />)}
                </div>
              ) : (
                <div className="text-xs text-slate-500 py-2 px-3 bg-slate-800/30 rounded-xl border border-slate-700/40">
                  {t("noAlerts")}
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </>
  );
}
