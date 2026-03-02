/**
 * AgentPanel — 4 onglets d'agents IA spécialisés
 *
 * Agents :
 *   custom  — Intelligence portuaire (formalités, tarifs marina)
 *   guard   — Sécurité maritime (piraterie, GMDSS, trafic)
 *   meteo   — Météorologie (fenêtres de passage, cyclones, vents)
 *   pirate  — Intelligence communautaire (Noonsite, forums cruisers)
 *
 * Chaque agent :
 *   - POST /agents/{type} avec le contexte du tronçon (LegContext)
 *   - Réponse streamée via SSE → affichage progressif
 *   - Fallback gracieux si le LLM est indisponible
 */

import { useState, useRef, useEffect } from "react";
import { Anchor, Shield, Cloud, Users, RefreshCw, AlertCircle } from "lucide-react";
import { useLang } from "../i18n/LangContext.jsx";

const API_URL = import.meta.env.VITE_API_URL;

// ── Configuration des agents ─────────────────────────────────────────────────

const AGENTS = [
  {
    key: "custom",
    icon: Anchor,
    color: "#0ea5e9",
    labelKey: "agentCustomLabel",
    labelFr: "Ports",
    titleKey: "agentCustomTitle",
    titleFr: "Intelligence Portuaire",
  },
  {
    key: "guard",
    icon: Shield,
    color: "#ef4444",
    labelKey: "agentGuardLabel",
    labelFr: "Sécurité",
    titleKey: "agentGuardTitle",
    titleFr: "Sécurité Maritime",
  },
  {
    key: "meteo",
    icon: Cloud,
    color: "#8b5cf6",
    labelKey: "agentMeteoLabel",
    labelFr: "Météo",
    titleKey: "agentMeteoTitle",
    titleFr: "Météorologie",
  },
  {
    key: "pirate",
    icon: Users,
    color: "#f59e0b",
    labelKey: "agentPirateLabel",
    labelFr: "Cruisers",
    titleKey: "agentPirateTitle",
    titleFr: "Intelligence Communautaire",
  },
];

// ── Rendu markdown minimal (gras + listes) ───────────────────────────────────

function renderMarkdown(text) {
  if (!text) return null;
  return text.split("\n").map((line, i) => {
    // Titre ##
    if (line.startsWith("## ")) {
      return <p key={i} className="text-[11px] font-bold text-white mt-2 mb-0.5">{line.slice(3)}</p>;
    }
    // Titre ###
    if (line.startsWith("### ")) {
      return <p key={i} className="text-[10px] font-semibold text-slate-300 mt-1.5">{line.slice(4)}</p>;
    }
    // Bullet
    if (line.startsWith("- ") || line.startsWith("• ")) {
      const content = renderInline(line.slice(2));
      return <p key={i} className="text-[10px] text-slate-300 leading-relaxed pl-2 before:content-['•'] before:mr-1.5 before:text-slate-500">{content}</p>;
    }
    // Ligne vide
    if (line.trim() === "") {
      return <div key={i} className="h-1" />;
    }
    // Normale
    return <p key={i} className="text-[10px] text-slate-300 leading-relaxed">{renderInline(line)}</p>;
  });
}

function renderInline(text) {
  // **gras**
  const parts = text.split(/\*\*(.*?)\*\*/g);
  return parts.map((part, i) =>
    i % 2 === 1 ? <strong key={i} className="text-white font-semibold">{part}</strong> : part
  );
}

// ── Composant ────────────────────────────────────────────────────────────────

/**
 * @param {object|null} legContext  — résultat de useLegContext
 * @param {string}      language   — "fr" | "en"
 */
export function AgentPanel({ legContext, language = "fr" }) {
  const { t } = useLang();
  const [activeTab, setActiveTab] = useState("custom");
  const [agentStates, setAgentStates] = useState({
    custom: { content: null, loading: false, error: null, source: null, loadedFor: null },
    guard:  { content: null, loading: false, error: null, source: null, loadedFor: null },
    meteo:  { content: null, loading: false, error: null, source: null, loadedFor: null },
    pirate: { content: null, loading: false, error: null, source: null, loadedFor: null },
  });
  const abortRefs = useRef({});

  // ── Identify the leg key (to detect when legContext changes) ────────────────
  const legKey = legContext
    ? `${legContext.fromStop}→${legContext.toStop}`
    : null;

  // ── Fetch on tab change or leg change ────────────────────────────────────
  useEffect(() => {
    if (!legContext || !legKey) return;
    const state = agentStates[activeTab];
    // Already loaded for this leg
    if (state.loadedFor === legKey && state.content) return;
    fetchAgent(activeTab, legContext);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, legKey]);

  async function fetchAgent(agentKey, ctx) {
    // Abort previous request for this tab if any
    if (abortRefs.current[agentKey]) {
      abortRefs.current[agentKey].abort();
    }
    const controller = new AbortController();
    abortRefs.current[agentKey] = controller;

    setAgentStates((prev) => ({
      ...prev,
      [agentKey]: { content: "", loading: true, error: null, source: null, loadedFor: null },
    }));

    try {
      const response = await fetch(`${API_URL}/agents/${agentKey}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          from_stop:    ctx.fromStop,
          to_stop:      ctx.toStop,
          lat:          ctx.snappedPosition[1],
          lon:          ctx.snappedPosition[0],
          nm_remaining: ctx.nmRemainingToStop,
          language,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const contentType = response.headers.get("content-type") || "";

      if (contentType.includes("text/event-stream")) {
        // ── SSE streaming ─────────────────────────────────────────────────
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let accumulated = "";
        let dataSource = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const raw = line.slice(6).trim();
              if (raw === "[DONE]") break;
              try {
                const obj = JSON.parse(raw);
                if (obj.token) {
                  accumulated += obj.token;
                  setAgentStates((prev) => ({
                    ...prev,
                    [agentKey]: { ...prev[agentKey], content: accumulated },
                  }));
                }
                if (obj.data_freshness) dataSource = obj.data_freshness;
              } catch {
                // Plain text token
                accumulated += raw;
                setAgentStates((prev) => ({
                  ...prev,
                  [agentKey]: { ...prev[agentKey], content: accumulated },
                }));
              }
            }
          }
        }
        setAgentStates((prev) => ({
          ...prev,
          [agentKey]: {
            content: accumulated,
            loading: false,
            error: null,
            source: dataSource,
            loadedFor: legKey,
          },
        }));
      } else {
        // ── JSON non-streaming ────────────────────────────────────────────
        const json = await response.json();
        setAgentStates((prev) => ({
          ...prev,
          [agentKey]: {
            content: json.content ?? JSON.stringify(json),
            loading: false,
            error: null,
            source: json.data_freshness ?? null,
            loadedFor: legKey,
          },
        }));
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      setAgentStates((prev) => ({
        ...prev,
        [agentKey]: {
          content: null,
          loading: false,
          error: err.message,
          source: null,
          loadedFor: null,
        },
      }));
    }
  }

  const activeAgent = AGENTS.find((a) => a.key === activeTab);
  const state = agentStates[activeTab];

  // ── No leg context ───────────────────────────────────────────────────────
  if (!legContext) {
    return (
      <div className="bg-slate-800/50 rounded-xl border border-slate-700/40 p-3">
        <p className="text-xs text-slate-500 text-center">
          {t ? t("agentNeedsCatamaran") : "Positionnez le catamaran sur la route pour activer les agents IA."}
        </p>
      </div>
    );
  }

  return (
    <div className="bg-slate-800/50 rounded-xl border border-slate-700/40 overflow-hidden">

      {/* ── Onglets ───────────────────────────────────────────────────────── */}
      <div className="flex border-b border-slate-700/40">
        {AGENTS.map((agent) => {
          const Icon = agent.icon;
          const isActive = activeTab === agent.key;
          const agentState = agentStates[agent.key];
          return (
            <button
              key={agent.key}
              onClick={() => setActiveTab(agent.key)}
              className={[
                "flex-1 flex items-center justify-center gap-1 py-2 text-[10px] font-semibold transition-all duration-150 border-b-2",
                isActive
                  ? "border-current bg-slate-700/40"
                  : "border-transparent text-slate-500 hover:text-slate-300 hover:bg-slate-700/20",
              ].join(" ")}
              style={{ color: isActive ? agent.color : undefined }}
              title={agent.titleFr}
            >
              {agentState.loading ? (
                <div className="w-2 h-2 rounded-full border border-current border-t-transparent animate-spin" />
              ) : (
                <Icon size={10} />
              )}
              <span className="hidden sm:inline">{t?.(agent.labelKey) ?? agent.labelFr}</span>
            </button>
          );
        })}
      </div>

      {/* ── Contenu ───────────────────────────────────────────────────────── */}
      <div className="p-3">

        {/* Titre + bouton refresh */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            {activeAgent && <activeAgent.icon size={11} style={{ color: activeAgent.color }} />}
            <span className="text-[10px] font-semibold text-slate-300">
              {t?.(activeAgent?.titleKey) ?? activeAgent?.titleFr}
            </span>
            {state.source && (
              <span
                className={[
                  "text-[9px] px-1.5 py-0.5 rounded-full font-medium",
                  state.source === "live"
                    ? "bg-green-900/40 text-green-400"
                    : state.source === "cached"
                    ? "bg-blue-900/40 text-blue-400"
                    : "bg-slate-700/60 text-slate-400",
                ].join(" ")}
              >
                {state.source === "live" ? "live" : state.source === "cached" ? "cache" : "training"}
              </span>
            )}
          </div>
          <button
            onClick={() => fetchAgent(activeTab, legContext)}
            disabled={state.loading}
            className="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-30"
            title={t ? t("refreshAgent") : "Actualiser"}
          >
            <RefreshCw size={10} className={state.loading ? "animate-spin" : ""} />
          </button>
        </div>

        {/* Context pill: from → to */}
        <div className="flex items-center gap-1 mb-2 text-[9px] text-slate-500">
          <span className="font-medium text-slate-400 truncate max-w-[80px]">{legContext.fromStop}</span>
          <span>→</span>
          <span className="font-medium text-slate-400 truncate max-w-[80px]">{legContext.toStop}</span>
          <span className="ml-auto flex-shrink-0">{legContext.nmRemainingToStop} nm</span>
        </div>

        {/* Contenu agent */}
        <div className="max-h-48 overflow-y-auto sidebar-scroll space-y-0.5">
          {state.loading && !state.content && (
            <div className="flex items-center gap-2 py-3 text-[10px] text-slate-400">
              <div className="w-3 h-3 rounded-full border-2 border-slate-600 border-t-slate-300 animate-spin flex-shrink-0" />
              <span>{t ? t("agentLoading") : "Analyse en cours…"}</span>
            </div>
          )}

          {state.error && !state.loading && (
            <div className="flex items-start gap-1.5 text-[10px] text-red-400">
              <AlertCircle size={11} className="flex-shrink-0 mt-0.5" />
              <span>{t ? t("agentError") : "Erreur de connexion à l'agent."} ({state.error})</span>
            </div>
          )}

          {state.content && (
            <div className="text-[10px] leading-relaxed">
              {renderMarkdown(state.content)}
            </div>
          )}
        </div>

        {/* Cursor clignotant pendant le streaming */}
        {state.loading && state.content && (
          <span className="inline-block w-1 h-3 bg-slate-400 animate-pulse ml-0.5 align-bottom" />
        )}
      </div>
    </div>
  );
}
