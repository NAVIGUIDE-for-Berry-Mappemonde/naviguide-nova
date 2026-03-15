# Spécification du contexte chat NAVIGUIDE

Chat avec accès à toutes les données de l'app selon le mode (normal vs simulation).
Résumer pour gérer la taille, ne pas tronquer.

---

## 1. Mode normal — inventaire des données disponibles

| Source | Données | Disponible côté |
|--------|---------|------------------|
| **plan** (expeditionPlan) | executive_briefing, voyage_statistics (total_distance_nm, total_segments, expedition_risk_level, anti_shipping_avg, high_risk_count, critical_count), critical_alerts (waypoint, risk_level, dominant_risk, scores), unified_geojson | Frontend |
| **segments** | Liste de legs : from, to, coords, distance_nm, nonMaritime, windPoints, wavePoints, currentPoints (données vent/vague/courant intégrées sur la route) | Frontend |
| **points** (itineraryPoints) | Waypoints : name, lat, lon, type (escale_obligatoire, point_intermediaire) | Frontend |
| **polarData** | expedition_id, boat_name, vmg_summary (TWS → upwind/downwind VMG, TWA, speed), created_at | Frontend |
| **maritimeLayers** | ZEE activées, Ports WPI activés, Balisage activé | Frontend |
| **Données satellites** | Wind, wave, current à une position (lat, lon) — API POST /wind, /wave, /current. En mode normal : pas de position unique ; on peut résumer les points windPoints/wavePoints/currentPoints déjà sur les segments | Frontend (déjà dans segments) ou API |
| **full_route_intelligence** | status, metadata (route plan Agent 1) | plan |
| **full_risk_assessment** | status, metadata (risk report Agent 3) | plan |

---

## 2. Mode simulation — inventaire des données disponibles

Tout le mode normal **plus** :

| Source | Données | Disponible côté |
|--------|---------|------------------|
| **legContext** | fromStop, toStop, nmCovered, nmRemainingToStop, etaHours, bearing, snappedPosition [lon, lat], speedKnots | Frontend |
| **Données satellites (leg)** | Wind, wave, current à legContext.snappedPosition — à récupérer via API /wind, /wave, /current | API (à appeler avec lat/lon du leg) |
| **Agent responses** | Réponses Meteo, Pirate, Guard, Custom pour ce leg (si l'utilisateur les a déjà demandées) | Frontend (optionnel, pas stocké par défaut) |

---

## 3. Schéma du contexte (JSON)

### 3.1 Mode normal — `ExpeditionContext`

```json
{
  "mode": "expedition",
  "language": "fr",
  "summary": {
    "total_distance_nm": 36484,
    "total_segments": 16,
    "expedition_risk_level": "LOW",
    "anti_shipping_avg": 0.85,
    "high_risk_count": 2,
    "critical_count": 0
  },
  "briefing": "BRIEFING EXPÉDITION BERRY-MAPPEMONDE...",
  "critical_alerts": [
    {
      "waypoint": "Papeete",
      "risk_level": "HIGH",
      "dominant_risk": "cyclone",
      "scores": { "weather_score": 0.3, "cyclone_score": 0.7, "piracy_score": 0, "medical_score": 0.1 }
    }
  ],
  "waypoints": [
    { "name": "La Rochelle", "lat": 46.16, "lon": -1.15, "type": "escale_obligatoire" }
  ],
  "legs_summary": [
    { "from": "La Rochelle", "to": "Ajaccio", "distance_nm": 680, "has_high_wind": false, "has_high_wave": false }
  ],
  "polar_summary": {
    "boat_name": "Leopard 46",
    "expedition_id": "berry-mappemonde-2026",
    "vmg_at_12kt": { "upwind_vmg": 5.2, "upwind_twa": 45, "downwind_vmg": 7.1, "downwind_twa": 150 }
  },
  "satellite_summary": "Vent/vague/courant : données intégrées sur la route (points échantillonnés). Pas de position unique en mode normal."
}
```

### 3.2 Mode simulation — `LegContext`

```json
{
  "mode": "simulation",
  "language": "fr",
  "leg": {
    "from_stop": "Fort-de-France",
    "to_stop": "Pointe-à-Pitre",
    "lat": 14.6,
    "lon": -61.0,
    "nm_covered": 4200,
    "nm_remaining_to_stop": 45,
    "eta_hours": 6,
    "bearing": 320,
    "speed_knots": 7.5
  },
  "expedition_summary": {
    "total_distance_nm": 36484,
    "expedition_risk_level": "LOW"
  },
  "briefing_excerpt": "Extrait du briefing pertinent pour ce bassin (2-3 phrases).",
  "alerts_on_leg": [
    { "waypoint": "Pointe-à-Pitre", "risk_level": "LOW", "dominant_risk": "weather" }
  ],
  "polar_summary": {
    "boat_name": "Leopard 46",
    "vmg_at_12kt": { "upwind_vmg": 5.2, "downwind_vmg": 7.1 }
  },
  "satellite_data": {
    "wind": { "speed_knots": 12, "direction": 85, "source": "Copernicus" },
    "wave": { "height_m": 1.2, "period_s": 8, "direction": 90 },
    "current": { "speed_knots": 0.3, "direction_deg": 270 }
  }
}
```

---

## 4. Règles de résumé (pour limiter les tokens)

- **briefing** : garder en entier (déjà structuré, ~1000 chars) ou résumer en 3–4 phrases si > 1500 chars.
- **critical_alerts** : max 5, format court.
- **legs_summary** : max 20 legs, une ligne par leg.
- **waypoints** : liste des noms + type, pas les coords sauf si pertinent.
- **polar_summary** : uniquement boat_name + VMG à TWS 12 (ou 10, 16 si demandé).
- **satellite_data** : en mode simulation, résumer wind/wave/current en une phrase par type.

---

## 5. System prompts

### 5.1 Mode normal — `system_prompt_expedition`

```
Tu es l'assistant NAVIGUIDE pour l'expédition Berry-Mappemonde — tour du monde en catamaran (36 000+ nm, territoires français d'outre-mer).

Tu as accès au contexte complet de l'expédition :
- Briefing exécutif (résumé skipper)
- Statistiques : distance totale, segments, niveau de risque, score anti-shipping
- Alertes critiques par waypoint
- Liste des escales et points intermédiaires
- Résumé des legs (from→to, distance, zones vent/vague fort)
- Polaires du bateau (VMG optimal upwind/downwind)
- Données satellite (vent, vague, courant) intégrées sur la route

Réponds aux questions du skipper en t'appuyant UNIQUEMENT sur ces données. Si une information n'est pas dans le contexte, dis-le clairement.
Sois concis, précis, utilise le vocabulaire maritime. Max 200 mots par réponse sauf si le skipper demande plus de détails.
Langue de réponse : {language}.
```

### 5.2 Mode simulation — `system_prompt_leg`

```
Tu es l'assistant NAVIGUIDE pour l'expédition Berry-Mappemonde. Le skipper est en mode simulation sur le leg actif.

Contexte du leg :
- De {from_stop} vers {to_stop}
- Position : {lat}° / {lon}°
- Distance restante : {nm_remaining_to_stop} nm
- ETA : {eta_hours} h
- Cap : {bearing}°
- Vitesse : {speed_knots} kt

Données satellite à la position : vent {wind_summary}, vague {wave_summary}, courant {current_summary}.
Résumé expédition : {total_nm} nm, risque {risk_level}.
Polaires : {boat_name}, VMG upwind/downwind à 12 kt.
Alertes sur ce leg : {alerts_summary}.

Réponds aux questions en te basant sur ce contexte. Priorise les infos du leg actif.
Concis, vocabulaire maritime. Max 200 mots. Langue : {language}.
```

---

## 6. Implémentation

1. **API** : Endpoint `POST /api/v1/chat` dans polar_api qui reçoit `{ mode, context, message, history }`. Le backend construit le system prompt à partir du context, appelle invoke_llm, renvoie la réponse.
2. **Frontend** : Fonction `buildChatContext(mode, plan, segments, polarData, legContext?, satelliteData?)` qui produit le JSON de contexte résumé. En mode simulation, le frontend appelle `/wind`, `/wave`, `/current` pour la position du leg avant de construire le contexte.
3. **Résumé** : Côté frontend, des helpers pour rester sous ~4k tokens : `summarizeBriefing`, `summarizeAlerts`, `summarizeLegs`, `summarizeSatellite`.
