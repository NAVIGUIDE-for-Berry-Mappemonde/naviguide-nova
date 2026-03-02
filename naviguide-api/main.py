import os
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import searoute as sr
from geographiclib.geodesic import Geodesic
from copernicus.getWind import get_wind_data_at_position
from utils.addWindProperties import add_wind_properties_to_route

# NOTE: FastAPI n'utilise PAS request et jsonify (qui sont de Flask)
# FastAPI gère automatiquement le JSON via Pydantic

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

# Récupérer les identifiants Copernicus depuis les variables d'environnement
COPERNICUS_USERNAME = os.getenv("COPERNICUS_USERNAME")
COPERNICUS_PASSWORD = os.getenv("COPERNICUS_PASSWORD")

# Vérifier que les identifiants sont bien définis
import logging
if not COPERNICUS_USERNAME or not COPERNICUS_PASSWORD:
    logging.warning(
        "⚠️  Copernicus credentials not set. Wind/wave data endpoints will be unavailable. "
        "Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD in the .env file to enable them."
    )

def searoute_with_exact_end(start, end):
    """
    Calcule une route maritime entre deux points et ajoute un segment géodésique
    jusqu'à la destination exacte si searoute s'arrête trop tôt.
    Gère correctement le passage de l'antiméridien (180°/-180°).
    """
    try:
        route = sr.searoute(start, end)
    except Exception as e:
        print(f"⚠️ Erreur searoute: {e}")
        return None

    if not route or "geometry" not in route:
        return None

    coords = route["geometry"]["coordinates"]
    last_point = coords[-1]

    # Calcul de la distance entre le dernier point et le vrai point d'arrivée
    geod = Geodesic.WGS84
    dist = geod.Inverse(last_point[1], last_point[0], end[1], end[0])["s12"]  # mètres

    # Si la route ne va pas jusqu'au point exact, on ajoute une courte ligne géodésique
    if dist > 1000:  # seuil = 1 km
        n_points = max(2, int(dist // 5000))  # environ 1 point tous les 5 km
        line = geod.InverseLine(last_point[1], last_point[0], end[1], end[0])
        extra_coords = []
        
        for i in range(1, n_points):
            pos = line.Position(i * line.s13 / (n_points - 1))
            lon = pos["lon2"]
            lat = pos["lat2"]
            
            # Normaliser la longitude pour gérer le passage de l'antiméridien
            if len(coords) > 0:
                prev_lon = coords[-1][0] if len(extra_coords) == 0 else extra_coords[-1][0]
                
                if lon - prev_lon > 180:
                    lon -= 360
                elif lon - prev_lon < -180:
                    lon += 360
            
            extra_coords.append([lon, lat])
        
        coords.extend(extra_coords)

    route["geometry"]["coordinates"] = coords
    return route


app = FastAPI(
    title="Searoute API",
    description="API pour calculer un itinéraire maritime entre deux coordonnées.",
    version="1.0.0",
)

# Autoriser ton frontend React à communiquer avec l'API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # À restreindre plus tard à ton domaine React
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Modèle Pydantic pour la requête de vent
class WindRequest(BaseModel):
    latitude: float
    longitude: float


@app.get("/")
def read_root():
    """Endpoint de base pour vérifier que l'API fonctionne"""
    return {
        "message": "Searoute API is running",
        "version": "1.0.0",
        "copernicus_configured": bool(COPERNICUS_USERNAME and COPERNICUS_PASSWORD)
    }


@app.get("/route")
def get_route(
    start_lat: float = Query(..., description="Latitude de départ"),
    start_lon: float = Query(..., description="Longitude de départ"),
    end_lat: float = Query(..., description="Latitude d'arrivée"),
    end_lon: float = Query(..., description="Longitude d'arrivée"),
    check_wind: bool = Query(False, description="Vérifier les vents forts sur la route"),
    sample_rate: int = Query(100, description="Vérifier 1 point tous les N points")
):
    """
    Calcule une route maritime entre deux points et renvoie le GeoJSON.
    Optionnellement, vérifie les vents forts sur la route.
    """
    start = (start_lon, start_lat)
    end = (end_lon, end_lat)

    try:
        route = searoute_with_exact_end(start, end)
        if route is None:
            raise HTTPException(status_code=404, detail="Route non trouvée")
        
        # Si demandé, vérifier les vents sur la route
        if check_wind:
            # Récupérer les credentials depuis l'environnement ou la config
            username = os.getenv("COPERNICUS_USERNAME")
            password = os.getenv("COPERNICUS_PASSWORD")
            
            route = add_wind_properties_to_route(
                route, 
                username=username, 
                password=password,
                sample_rate=sample_rate
            )
        
        return route
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/wind")
def get_wind(request: WindRequest):
    """
    Récupère les données de vent à une position donnée via Copernicus Marine
    
    Args:
        request: WindRequest contenant latitude et longitude
    
    Returns:
        dict: Données de vent (vitesse, direction, etc.)
    """
    try:
        wind_data = get_wind_data_at_position(
            latitude=request.latitude,
            longitude=request.longitude,
            username=COPERNICUS_USERNAME,
            password=COPERNICUS_PASSWORD
        )
        
        if wind_data is None:
            raise HTTPException(
                status_code=404, 
                detail="Aucune donnée de vent disponible pour cette position"
            )
        
        return wind_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération du vent: {str(e)}")

# Point d'entrée pour lancer l'application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)