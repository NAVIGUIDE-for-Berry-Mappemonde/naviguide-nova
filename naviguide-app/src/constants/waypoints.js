// Waypoint flag mappings for NAVIGUIDE itinerary stopovers
import berry             from "../assets/img/flags/berry.png";
import corse             from "../assets/img/flags/corse.png";
import france            from "../assets/img/flags/france.png";
import guadeloupe        from "../assets/img/flags/guadeloupe.png";
import guyane            from "../assets/img/flags/guyane.png";
import martinique        from "../assets/img/flags/martinique.png";
import mayotte           from "../assets/img/flags/mayotte.png";
import nouvelleCaledonie from "../assets/img/flags/nouvelle_caledonie.png";
import polynesie         from "../assets/img/flags/polynesie.png";
import reunion           from "../assets/img/flags/reunion.png";
import saintBarthelemy   from "../assets/img/flags/saint_barthelemy.png";
import saintMartin       from "../assets/img/flags/saint_martin.png";
import saintPierreMiquelon from "../assets/img/flags/saint_pierre_miquelon.png";
import taaf              from "../assets/img/flags/taaf.png";
import wallisFutuna      from "../assets/img/flags/wallis_futuna.png";

// Map waypoint names (as stored in GeoJSON properties.name) to flag images
const WAYPOINT_FLAGS = {
  "Saint-Maur (Berry, Indre)":             berry,
  "La Rochelle":                            france,
  "Ajaccio (Corse)":                        corse,
  "Fort-de-France (Martinique)":            martinique,
  "Pointe-à-Pitre (Guadeloupe)":            guadeloupe,
  "Gustavia (Saint-Barthélemy)":            saintBarthelemy,
  "Marigot (Saint-Martin)":                 saintMartin,
  "Saint-Pierre (Saint-Pierre-et-Miquelon)": saintPierreMiquelon,
  "Cayenne (Guyane)":                       guyane,
  "Papeete (Polynésie française)":          polynesie,
  "Mata-Utu (Wallis-et-Futuna)":            wallisFutuna,
  "Nouméa (Nouvelle-Calédonie)":            nouvelleCaledonie,
  "Dzaoudzi (Mayotte)":                     mayotte,
  "Tromelin (TAAF)":                        taaf,
  "Saint-Gilles (La Réunion)":              reunion,
  "Europa (TAAF)":                          taaf,
};

/**
 * Returns the flag image asset for the given waypoint name, or null if none.
 * @param {string} name - The waypoint name (from GeoJSON properties.name)
 * @returns {string|null} - Flag image URL or null
 */
export function getFlagForWaypoint(name) {
  return WAYPOINT_FLAGS[name] || null;
}
