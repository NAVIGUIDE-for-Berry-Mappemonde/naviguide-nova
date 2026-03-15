#!/usr/bin/env bash
# =============================================================================
# NAVIGUIDE Hackathon — Téléchargement données géospatiales
# - GEBCO 2024 bathymétrie (~7 GB) — si pas déjà présent
# - AIS World Bank Global Ship Density (~510 MB)
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(dirname "$SCRIPT_DIR")/data"
GEBCO_DIR="$DATA_DIR/gebco"
AIS_DIR="$DATA_DIR/ais_worldbank"

mkdir -p "$GEBCO_DIR" "$AIS_DIR"

# ── GEBCO 2024 (déjà téléchargé = skip) ─────────────────────────────────────
GEBCO_FILE="$GEBCO_DIR/GEBCO_2024_CF.nc"
if [ -f "$GEBCO_FILE" ]; then
  echo "✓ GEBCO déjà présent: $GEBCO_FILE"
else
  echo "⚠ GEBCO: télécharger manuellement depuis https://www.gebco.net/data_and_products/gridded_bathymetry_data/gebco_2024/"
  echo "  Placer GEBCO_2024_CF.nc dans $GEBCO_DIR/"
fi

# ── AIS World Bank — Global Ship Density (~510 MB) ──────────────────────────
AIS_URL="https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0045406/shipdensity_global.zip"
AIS_ZIP="$AIS_DIR/shipdensity_global.zip"

if [ -f "$AIS_ZIP" ] || ls "$AIS_DIR"/*.tif 1>/dev/null 2>&1; then
  echo "✓ AIS World Bank déjà présent dans $AIS_DIR"
else
  echo "Téléchargement AIS World Bank Global Ship Density (~510 MB)..."
  curl -L -o "$AIS_ZIP" "$AIS_URL" && {
    echo "Extraction..."
    unzip -o "$AIS_ZIP" -d "$AIS_DIR"
    echo "✓ AIS téléchargé et extrait"
  } || echo "⚠ Échec téléchargement AIS"
fi

echo ""
echo "Terminé. data/gebco/ et data/ais_worldbank/"
