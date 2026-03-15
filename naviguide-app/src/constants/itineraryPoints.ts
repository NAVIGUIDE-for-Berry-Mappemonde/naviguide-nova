import berry from "../assets/img/flags/berry.png";
import corse from "../assets/img/flags/corse.png";
import france from "../assets/img/flags/france.png";
import guadeloupe from "../assets/img/flags/guadeloupe.png";
import guyane from "../assets/img/flags/guyane.png";
import martinique from "../assets/img/flags/martinique.png";
import mayotte from "../assets/img/flags/mayotte.png";
import nouvelleCaledonie from "../assets/img/flags/nouvelle_caledonie.png";
import polynesie from "../assets/img/flags/polynesie.png";
import reunion from "../assets/img/flags/reunion.png";
import saintBarthelemy from "../assets/img/flags/saint_barthelemy.png";
import saintMartin from "../assets/img/flags/saint_martin.png";
import saintPierreMiquelon from "../assets/img/flags/saint_pierre_miquelon.png";
import taaf from "../assets/img/flags/taaf.png";
import wallisFutuna from "../assets/img/flags/wallis_futuna.png";

export const ITINERARY_POINTS = [
  {
    name: "Saint-Maur (Berry, Indre)",
    lat: 46.8075,
    lon: 1.6358,
    flag: berry,
  },
  {
    // snapped 1.29 km offshore (was on land in NE mask)
    name: "La Rochelle",
    lat: 46.1541,
    lon: -1.167,
    flag: france,
  },
  {
    name: "Point intermédiaire Avant Corse",
    lat: 41.181,
    lon: 8.438,
    flag: "",
  },
  {
    name: "Ajaccio (Corse)",
    lat: 41.9192,
    lon: 8.7386,
    flag: corse,
  },
  {
    name: "Point intermédiaire Après Corse",
    lat: 43.30582034342319,
    lon: 8.664023850795274,
    flag: "",
  },
  {
    name: "Point intermédiaire Après Corse",
    lat: 42.220925515885995,
    lon: 9.843755668349303,
    flag: "",
  },
  {
    name: "Iles Canari",
    lat: 29.325,
    lon: -15.181,
    flag: "",
  },
  {
    name: "Point intermédiaire Cap Verde",
    lat: 13.919,
    lon: -24.531,
    flag: "",
  },
  {
    name: "Sainte Lucie",
    lat: 13.499,
    lon: -61.498,
    flag: "",
  },
  {
    // snapped 1.66 km offshore — south into Fort-de-France bay entrance
    name: "Fort-de-France (Martinique)",
    lat: 14.5887,
    lon: -61.0731,
    flag: martinique,
  },
  {
    // snapped 0.77 km offshore — into harbour approach channel
    name: "Pointe-à-Pitre (Guadeloupe)",
    lat: 16.2365,
    lon: -61.5381,
    flag: guadeloupe,
  },
  {
    // snapped 0.77 km offshore
    name: "Gustavia (Saint-Barthélemy)",
    lat: 17.8912,
    lon: -62.8548,
    flag: saintBarthelemy,
  },
  {
    // snapped 1.97 km offshore — north into Simpson Bay Lagoon approach
    name: "Marigot (Saint-Martin)",
    lat: 18.0829,
    lon: -63.0922,
    flag: saintMartin,
  },
  {
    // snapped 2.22 km offshore — west into the Cayenne river estuary
    name: "Cayenne (Guyane)",
    lat: 4.9333,
    lon: -52.3533,
    flag: guyane,
  },
  {
    // snapped 0.79 km offshore — east into harbour (segment maritime découplé)
    name: "Halifax (Nouvelle-Écosse)",
    lat: 44.6488,
    lon: -63.5652,
    flag: "",
  },
  {
    // snapped 1.27 km offshore
    name: "Saint-Pierre (Saint-Pierre-et-Miquelon)",
    lat: 46.7761,
    lon: -56.1628,
    flag: saintPierreMiquelon,
  },
  {
    // snapped 4.55 km offshore — north into Papeete roadstead
    name: "Papeete (Polynésie française)",
    lat: -17.5116,
    lon: -149.5685,
    flag: polynesie,
  },
  {
    // snapped 3.43 km offshore
    name: "Mata-Utu (Wallis-et-Futuna)",
    lat: -13.2725,
    lon: -176.2036,
    flag: wallisFutuna,
  },
  {
    // snapped 2.21 km offshore — south into Nouméa lagoon
    name: "Nouméa (Nouvelle-Calédonie)",
    lat: -22.2958,
    lon: 166.4572,
    flag: nouvelleCaledonie,
  },
  {
    // Torres Strait — north of Cape York tip, in the Great North East Channel.
    // Ensures the pipeline routes correctly through the strait without generating
    // a spurious Coral Sea circumnavigation detour.
    name: "Point intermédiaire détroit de Torres",
    lat: -10.543294,
    lon: 142.135679,
    flag: "",
  },
  {
    name: "Point intermédiaire haut Australie",
    lat: -8.975505823887872,
    lon: 135.7760571767464,
    flag: "",
  },
  {
    name: "Point intermédiaire haut Australie",
    lat: -9.365171092340532,
    lon: 105.09261288903605,
    flag: "",
  },
  {
    name: "Point intermédiaire haut Australie",
    lat: 6.372651054775204,
    lon: 88.60640235539029,
    flag: "",
  },
  {
    // snapped 9.96 km offshore — east coast of Sri Lanka
    name: "Sri Lanka",
    lat: 6.7907,
    lon: 81.8354,
    flag: "",
  },
  {
    name: "Maldives",
    lat: -0.011960820899744817,
    lon: 73.34654992443669,
    flag: "",
  },
  {
    // snapped 0.78 km offshore
    name: "Seichelles",
    lat: -4.7905,
    lon: 55.5358,
    flag: "",
  },
  {
    // snapped 0.78 km offshore
    name: "Dzaoudzi (Mayotte)",
    lat: -12.7921,
    lon: 45.27,
    flag: mayotte,
  },
  {
    // snapped 0.54 km offshore — Tromelin island approach
    name: "Tromelin (TAAF)",
    lat: -15.89,
    lon: 54.525,
    flag: taaf,
  },
  {
    name: "Saint-Gilles (La Réunion)",
    lat: -21.0594,
    lon: 55.2242,
    flag: reunion,
  },
  {
    // snapped 0.55 km offshore
    name: "Europa (TAAF)",
    lat: -22.3685,
    lon: 40.3476,
    flag: taaf,
  },
  {
    name: "Point intermédiaire Cap de la Bonne Espérance",
    lat: -33.582958207198814,
    lon: 14.083704115920511,
    flag: "",
  },
  {
    // snapped 3.85 km offshore — Sainte-Hélène island
    name: "Point intermédiaire Sainte Hélène",
    lat: -15.9165,
    lon: -5.7392,
    flag: "",
  },
  {
    // snapped 2.76 km offshore — Ascension island
    name: "Point intermédiaire Ascension",
    lat: -7.9692,
    lon: -14.3291,
    flag: "",
  },
  {
    name: "Point intermédiaire Ascension - Cap Verde",
    lat: 4.649408270655059,
    lon: -24.595104128163598,
    flag: "",
  },
  {
    name: "Point intermédiaire Cap Verde",
    lat: 13.919,
    lon: -24.531,
    flag: "",
  },
  {
    // snapped 1.29 km offshore (same correction as outbound La Rochelle)
    name: "La Rochelle",
    lat: 46.1541,
    lon: -1.167,
    flag: france,
  },
];

// export const ITINERARY_POINTS = [
//   {
//     name: "Saint-Maur (Berry, Indre)",
//     lat: 46.8075,
//     lon: 1.6358,
//     flag: berry,
//   },
//   {
//     name: "La Rochelle",
//     lat: 46.1591,
//     lon: -1.152,
//     flag: france,
//   },
//   {
//     name: "Point intermédiaire Avant Corse",
//     lat: 41.055680018451,
//     lon: 7.571686294801026,
//     flag: "",
//   },
//   {
//     name: "Ajaccio (Corse)",
//     lat: 41.9192,
//     lon: 8.7386,
//     flag: corse,
//   },
//   {
//     name: "Point intermédiaire Après Corse",
//     lat: 43.30582034342319,
//     lon: 8.664023850795274,
//     flag: "",
//   },
// ];
