/**
 * CatamaranMarker — Draggable MapLibre marker representing the catamaran
 *
 * - Shown only when simulationMode === true
 * - Initial position : first route point (Saint-Maur departure, set by App.jsx)
 * - Drag (real-time) → snaps to the route via useLegContext in App.jsx
 * - Click → opens the metrics popup (handled by App.jsx)
 *
 * Icon: catamaran.jpg — 240×240, no EXIF tag, white background removed via Canvas.
 *   Raw pixel layout: bow → RIGHT, mast → UP
 *
 * Orientation strategy (avoids upside-down appearance from large rotations):
 *
 *   East half  (sin(bearing) ≥ 0, i.e. bearing ∈ [0°,180°]):
 *     → natural image (bow RIGHT), tilt = bearing − 90°
 *     → max tilt ±90° → image never inverts
 *
 *   West half  (sin(bearing) < 0, i.e. bearing ∈ (180°,360°)):
 *     → flip image horizontally → bow becomes LEFT, tilt = bearing − 270°
 *     → max tilt ±90° → image never inverts
 *
 *   Southern hemisphere (latitude < 0):
 *     → additionally scaleY(-1) to flip mast to the bottom of the image
 *     → CSS transform order: rotate(tilt) [scaleX(-1)] [scaleY(-1)]
 *       CSS applies transforms left→right (updating the coord system), so
 *       scaleY/-X are applied first in image space, then the tilt rotation.
 */

import { useEffect, useState } from "react";
import { Marker } from "react-map-gl/maplibre";
import { useLang } from "../i18n/LangContext.jsx";
import catamaranImg from "../assets/img/catamaran.jpg";

// ── Remove white background ───────────────────────────────────────────────────
// Near-white pixels are made transparent via Canvas color-keying.
// imageOrientation:'none' is kept for safety (no EXIF on this file, but harmless).
function useTransparentPng(src, threshold = 235) {
  const [png, setPng] = useState(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res    = await fetch(src);
        const blob   = await res.blob();
        const bitmap = await createImageBitmap(blob, { imageOrientation: "none" });
        if (cancelled) { bitmap.close(); return; }
        const canvas = document.createElement("canvas");
        canvas.width  = bitmap.width;
        canvas.height = bitmap.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(bitmap, 0, 0);
        bitmap.close();
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const d = imageData.data;
        for (let i = 0; i < d.length; i += 4) {
          if (d[i] > threshold && d[i + 1] > threshold && d[i + 2] > threshold) {
            d[i + 3] = 0;
          }
        }
        ctx.putImageData(imageData, 0, 0);
        if (!cancelled) setPng(canvas.toDataURL("image/png"));
      } catch {
        // Fallback: draw via <img> if fetch/createImageBitmap fails
        if (cancelled) return;
        const img = new Image();
        img.onload = () => {
          if (cancelled) return;
          const canvas = document.createElement("canvas");
          canvas.width  = img.width;
          canvas.height = img.height;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0);
          const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const d = imageData.data;
          for (let i = 0; i < d.length; i += 4) {
            if (d[i] > threshold && d[i + 1] > threshold && d[i + 2] > threshold)
              d[i + 3] = 0;
          }
          ctx.putImageData(imageData, 0, 0);
          setPng(canvas.toDataURL("image/png"));
        };
        img.src = src;
      }
    })();
    return () => { cancelled = true; };
  }, [src]);
  return png;
}

// ── Catamaran icon with correct bearing + hemisphere orientation ──────────────
//
// Raw image: bow RIGHT (90°), mast UP.
//
// East half  sin(bearing) ≥ 0  →  natural (bow RIGHT) + rotate(bearing − 90°)
// West half  sin(bearing) < 0  →  scaleX(-1) (bow → LEFT) + rotate(bearing − 270°)
// South hemi latitude < 0      →  additionally scaleY(-1)   (mast → bottom)
//
// CSS transform string: "rotate(tilt) [scaleX(-1)] [scaleY(-1)]"
// CSS applies left→right to the coordinate system, so the rightmost
// scale is applied first in image-local space, then the rotation.
// Max tilt is ±90° in both halves → image never rotates fully upside-down.
//
function CatamaranIcon({ size = 56, bearing = 0, southernHemisphere = false }) {
  const png = useTransparentPng(catamaranImg);
  if (!png) return null;

  const bearingRad = (bearing * Math.PI) / 180;
  const goingEast  = Math.sin(bearingRad) >= 0;  // bearing in [0°, 180°]
  const tilt       = goingEast ? bearing - 90 : bearing - 270;

  // scaleX(-1) MUST come before rotate() so the flip happens in image-local
  // space and the subsequent rotation is applied to the already-flipped image.
  // Wrong order (rotate then scaleX) mirrors the rotation axis and produces
  // a bearing that is off by up to 180° for westward headings.
  const parts = [];
  if (!goingEast)         parts.push("scaleX(-1)");  // flip H first (bow → LEFT)
  parts.push(`rotate(${tilt}deg)`);                  // then tilt
  if (southernHemisphere) parts.push("scaleY(-1)");

  return (
    <img
      src={png}
      alt="catamaran"
      draggable={false}
      style={{
        width: size,
        height: size,
        objectFit: "contain",
        display: "block",
        userSelect: "none",
        pointerEvents: "none",
        cursor: "grab",
        transform: parts.join(" "),
        transition: "transform 0.35s ease",
        flexShrink: 0,
      }}
    />
  );
}

// ── Composant principal ──────────────────────────────────────────────────────

/**
 * @param {number}   latitude   — current latitude of the catamaran (snapped)
 * @param {number}   longitude  — current longitude of the catamaran (snapped)
 * @param {number}   bearing    — heading in degrees 0–360; 0 = north
 * @param {Function} onDragEnd  — callback({ lat, lon }) called during drag AND on release
 *                                so useLegContext snaps in real-time as the user drags
 * @param {Function} onClick    — callback to open the metrics popup
 */
export function CatamaranMarker({
  latitude,
  longitude,
  bearing = 0,
  onDragEnd,
  onClick,
}) {
  const { t } = useLang();

  // Single handler used for both onDrag (real-time) and onDragEnd (release).
  // Calling onDragEnd on every drag event lets App.jsx → useLegContext snap
  // the position to the nearest route point on every pointer move, so the
  // catamaran visually "slides along the route" rather than jumping at drop.
  const handlePosition = (event) => {
    const { lng, lat } = event.lngLat;
    if (onDragEnd) onDragEnd({ lat, lon: lng });
  };

  return (
    <Marker
      latitude={latitude}
      longitude={longitude}
      draggable
      onDrag={handlePosition}     // ← real-time route snap during drag
      onDragEnd={handlePosition}  // ← final snap on pointer release
      anchor="center"
    >
      <div
        onClick={onClick}
        title={
          t
            ? t("simulationMarkerTitle")
            : "Catamaran — cliquez pour les métriques"
        }
        style={{ pointerEvents: "auto" }}
      >
        <CatamaranIcon
          bearing={bearing}
          southernHemisphere={latitude < 0}
        />
      </div>
    </Marker>
  );
}
