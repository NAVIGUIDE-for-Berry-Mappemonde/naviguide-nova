/**
 * buildChatContext — Construit le contexte résumé pour le chat NAVIGUIDE
 *
 * Mode expedition : plan, briefing, alerts, segments, polar, satellite (résumé route)
 * Mode simulation : leg, expedition summary, polar, satellite (données à la position)
 *
 * Résumer pour limiter les tokens, ne pas tronquer brutalement.
 */

const MAX_BRIEFING_CHARS = 2000;
const MAX_ALERTS = 5;
const MAX_LEGS = 20;
const MAX_WAYPOINTS = 30;

function summarizeBriefing(text) {
  if (!text || typeof text !== "string") return "";
  if (text.length <= MAX_BRIEFING_CHARS) return text;
  // Couper au dernier point avant la limite pour garder des phrases complètes
  const cut = text.slice(0, MAX_BRIEFING_CHARS);
  const lastDot = cut.lastIndexOf(".");
  return (lastDot > MAX_BRIEFING_CHARS * 0.7 ? cut.slice(0, lastDot + 1) : cut + "...");
}

function summarizeAlerts(alerts) {
  if (!Array.isArray(alerts)) return [];
  return alerts.slice(0, MAX_ALERTS).map((a) => ({
    waypoint: a.waypoint ?? a.name,
    risk_level: a.risk_level,
    dominant_risk: a.dominant_risk,
    scores: a.scores,
  }));
}

function summarizeLegs(segments) {
  if (!Array.isArray(segments)) return [];
  return segments.slice(0, MAX_LEGS).map((s) => ({
    from: s.from?.name ?? s.from,
    to: s.to?.name ?? s.to,
    distance_nm: s.distance_nm ?? estimateSegmentNm(s),
    has_high_wind: !!(s.windPoints && s.windPoints.length > 0),
    has_high_wave: !!(s.wavePoints && s.wavePoints.length > 0),
    has_currents: !!(s.currentPoints && s.currentPoints.length > 0),
  }));
}

function estimateSegmentNm(seg) {
  if (seg.distance_nm != null) return seg.distance_nm;
  const coords = seg.coords;
  if (!coords || coords.length < 2) return 0;
  let nm = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    nm += haversineNm(coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]);
  }
  return Math.round(nm);
}

function haversineNm(lat1, lon1, lat2, lon2) {
  const R = 3440.065;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function summarizeSatelliteFromSegments(segments) {
  if (!Array.isArray(segments)) return "";
  let windCount = 0;
  let waveCount = 0;
  let currentCount = 0;
  for (const s of segments) {
    windCount += (s.windPoints || []).length;
    waveCount += (s.wavePoints || []).length;
    currentCount += (s.currentPoints || []).length;
  }
  const parts = [];
  if (windCount > 0) parts.push(`${windCount} point(s) vent fort`);
  if (waveCount > 0) parts.push(`${waveCount} point(s) vagues hautes`);
  if (currentCount > 0) parts.push(`${currentCount} point(s) courants`);
  return parts.length ? parts.join(" ; ") : "Données intégrées sur la route (échantillonnage).";
}

function polarSummary(polarData) {
  if (!polarData) return {};
  const vmg = polarData.vmg_summary || {};
  const entry12 = vmg["12"] || vmg["10"] || {};
  const uw = entry12.upwind || {};
  const dw = entry12.downwind || {};
  return {
    boat_name: polarData.boat_name,
    expedition_id: polarData.expedition_id,
    vmg_at_12kt: {
      upwind_vmg: uw.vmg,
      upwind_twa: uw.twa,
      downwind_vmg: dw.vmg,
      downwind_twa: dw.twa,
    },
  };
}

/**
 * @param {Object} options
 * @param {"expedition"|"simulation"} options.mode
 * @param {Object} options.plan — expeditionPlan
 * @param {Array} options.segments — route segments
 * @param {Array} options.points — itinerary points
 * @param {Object} options.polarData — polar data
 * @param {Object} options.legContext — useLegContext result (simulation only)
 * @param {Object} options.satelliteData — { wind, wave, current } at position (simulation only)
 * @param {string} options.language — "fr" | "en"
 */
export function buildChatContext({
  mode,
  plan,
  segments,
  points,
  polarData,
  legContext,
  satelliteData,
  language = "fr",
}) {
  const stats = plan?.voyage_statistics || {};
  const polar = polarSummary(polarData);

  if (mode === "expedition") {
    return {
      mode: "expedition",
      language,
      summary: {
        total_distance_nm: stats.total_distance_nm,
        total_segments: stats.total_segments,
        expedition_risk_level: stats.expedition_risk_level,
        anti_shipping_avg: stats.anti_shipping_avg,
        high_risk_count: stats.high_risk_count,
        critical_count: stats.critical_count,
      },
      briefing: summarizeBriefing(plan?.executive_briefing || ""),
      critical_alerts: summarizeAlerts(plan?.critical_alerts || []),
      waypoints: (points || []).slice(0, MAX_WAYPOINTS).map((p) => ({
        name: p.name,
        lat: p.lat,
        lon: p.lon,
        type: p.type || p.flag ? "escale_obligatoire" : "point_intermediaire",
      })),
      legs_summary: summarizeLegs(segments),
      polar_summary: polar,
      satellite_summary: summarizeSatelliteFromSegments(segments),
    };
  }

  // mode === "simulation"
  const [lon, lat] = legContext?.snappedPosition || [null, null];
  const alertsOnLeg = (plan?.critical_alerts || []).filter((a) => {
    const wp = (a.waypoint || a.name || "").toLowerCase();
    const from = (legContext?.fromStop || "").toLowerCase();
    const to = (legContext?.toStop || "").toLowerCase();
    return wp.includes(from) || wp.includes(to);
  });

  return {
    mode: "simulation",
    language,
    leg: {
      from_stop: legContext?.fromStop,
      to_stop: legContext?.toStop,
      lat: lat ?? legContext?.snappedPosition?.[1],
      lon: lon ?? legContext?.snappedPosition?.[0],
      nm_covered: legContext?.nmCovered,
      nm_remaining_to_stop: legContext?.nmRemainingToStop,
      eta_hours: legContext?.etaHours,
      bearing: legContext?.bearing,
      speed_knots: legContext?.speedKnots,
    },
    expedition_summary: {
      total_distance_nm: stats.total_distance_nm,
      expedition_risk_level: stats.expedition_risk_level,
    },
    briefing_excerpt: summarizeBriefing(plan?.executive_briefing || "").slice(0, 500),
    alerts_on_leg: summarizeAlerts(alertsOnLeg),
    polar_summary: polar,
    satellite_data: satelliteData || {},
  };
}
