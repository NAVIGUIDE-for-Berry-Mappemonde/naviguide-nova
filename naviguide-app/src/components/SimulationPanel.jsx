/**
 * SimulationPanel — Affiche les métriques de progression du catamaran
 *
 * Calcul purement géométrique depuis useLegContext.
 * Aucun appel API — données disponibles instantanément au drag.
 *
 * Props:
 *   legContext   — objet LegContext depuis useLegContext hook
 *   onClose      — callback pour désactiver le mode simulation
 *   onAdvance    — callback pour avancer au milieu du prochain segment
 *   canAdvance   — boolean, désactive le bouton si fin de route atteinte
 */

import { Navigation, Clock, Compass, Map as MapIcon, X, ChevronLeft, ChevronRight } from "lucide-react";
import { useLang } from "../i18n/LangContext.jsx";

// ── Formatage ────────────────────────────────────────────────────────────────

function formatEta(hours) {
  if (hours == null || isNaN(hours)) return "—";
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  if (h === 0) return `${m}min`;
  if (m === 0) return `${h}h`;
  return `${h}h${String(m).padStart(2, "0")}`;
}

function formatNm(nm) {
  if (nm == null) return "—";
  return `${nm.toLocaleString()} nm`;
}

function formatBearing(deg) {
  if (deg == null) return "—";
  const dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"];
  const idx = Math.round(deg / 22.5) % 16;
  return `${Math.round(deg)}° ${dirs[idx]}`;
}

// ── Boutons Précédent / Suivant ──────────────────────────────────────────────

function PrevNextButtons({ onPrev, canPrev, onNext, canNext }) {
  const { t } = useLang();
  const btnBase = "flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-[10px] font-semibold transition-all duration-150 select-none border";
  const btnActive = "text-white cursor-pointer";
  const btnDisabled = "bg-slate-700/30 text-white/25 border-white/5 cursor-not-allowed";
  return (
    <div className="px-2 pb-2 pt-1 flex gap-1.5">
      <button
        onClick={onPrev}
        disabled={!canPrev}
        className={[btnBase, canPrev ? `${btnActive} bg-slate-700/60 border-slate-500/50 hover:bg-slate-600/70` : btnDisabled].join(" ")}
        title={t("previous")}
      >
        <ChevronLeft size={10} />
        <span>{t("previous")}</span>
      </button>
      <button
        onClick={onNext}
        disabled={!canNext}
        className={[btnBase, canNext ? `${btnActive} bg-cyan-700/60 border-cyan-500/50 hover:bg-cyan-600/70` : btnDisabled].join(" ")}
        title={t("next")}
      >
        <span>{t("next")}</span>
        <ChevronRight size={10} />
      </button>
    </div>
  );
}

// ── Composant principal ──────────────────────────────────────────────────────

export function SimulationPanel({ legContext, onClose, onPrev, canPrev, onNext, canNext }) {
  const { t } = useLang();

  if (!legContext) {
    return (
      <div className="bg-slate-800/60 rounded-xl p-3 border border-blue-700/30">
        <div className="text-xs text-slate-400 text-center">
          {t("simulationDragPrompt")}
        </div>
        {/* Boutons nav visibles même sans legContext (catamaran sur La Rochelle) */}
        <div className="mt-2">
          <PrevNextButtons onPrev={onPrev} canPrev={canPrev} onNext={onNext} canNext={canNext} />
        </div>
      </div>
    );
  }

  const {
    fromStop, toStop,
    nmCovered, nmRemainingToStop,
    etaHours, bearing, speedKnots,
  } = legContext;

  return (
    <div className="bg-slate-800/70 rounded-xl border border-blue-600/30 overflow-hidden">

      {/* Header tronçon actif */}
      <div className="flex items-center justify-between px-3 py-2 bg-blue-900/30 border-b border-blue-700/20">
        <div className="flex items-center gap-1.5 min-w-0">
          <Navigation size={11} className="text-blue-400 flex-shrink-0" />
          <span className="text-[10px] font-semibold text-blue-300 truncate">
            {fromStop}
          </span>
          <span className="text-white/30 text-[10px]">→</span>
          <span className="text-[10px] font-semibold text-cyan-300 truncate">
            {toStop}
          </span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="text-white/30 hover:text-white/70 transition-colors flex-shrink-0 ml-1"
            title={t("exitSimulation")}
          >
            <X size={12} />
          </button>
        )}
      </div>

      {/* Grille métriques */}
      <div className="grid grid-cols-2 gap-px bg-slate-700/20 p-0.5">

        {/* NM restants */}
        <div className="bg-slate-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <MapIcon size={10} className="text-cyan-400" />
            <span className="text-[9px] text-slate-400 uppercase tracking-wider">
              {t("nmRemaining")}
            </span>
          </div>
          <span className="text-sm font-bold text-white">{formatNm(nmRemainingToStop)}</span>
        </div>

        {/* ETA */}
        <div className="bg-slate-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <Clock size={10} className="text-amber-400" />
            <span className="text-[9px] text-slate-400 uppercase tracking-wider">
              {t("eta")}
            </span>
          </div>
          <span className="text-sm font-bold text-white">{formatEta(etaHours)}</span>
          <span className="text-[9px] text-slate-500">@ {speedKnots} kt</span>
        </div>

        {/* NM parcourus */}
        <div className="bg-slate-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <Navigation size={10} className="text-green-400" />
            <span className="text-[9px] text-slate-400 uppercase tracking-wider">
              {t("nmCovered")}
            </span>
          </div>
          <span className="text-sm font-bold text-white">{formatNm(nmCovered)}</span>
        </div>

        {/* Cap */}
        <div className="bg-slate-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <Compass size={10} className="text-purple-400" />
            <span className="text-[9px] text-slate-400 uppercase tracking-wider">
              {t("bearing")}
            </span>
          </div>
          <span className="text-sm font-bold text-white">{formatBearing(bearing)}</span>
        </div>

      </div>

      {/* Boutons Précédent / Suivant */}
      <PrevNextButtons onPrev={onPrev} canPrev={canPrev} onNext={onNext} canNext={canNext} />

    </div>
  );
}
