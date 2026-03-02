/**
 * NAVIGUIDE Proxy Server
 * Serves the built React frontend and proxies all backend service calls.
 *
 *  /route, /wind, /wave  → Python base API  (port 8000)
 *  /anti-shipping-route  → Agent 1          (port 8001)
 *  /berry-mappemonde-*,
 *  /expedition/*         → Orchestrator     (port 8002)
 *  /assess-risks,
 *  /assess-route         → Agent 3          (port 8003)
 */
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");
const path = require("path");

const app  = express();
const PORT = process.env.PORT || 3005;

const proxy = (target) =>
  createProxyMiddleware({ target, changeOrigin: true, logLevel: "silent" });

// --- Backend service proxies ---
app.use(["/route", "/wind", "/wave"],        proxy("http://localhost:8000"));
app.use(["/anti-shipping-route"],            proxy("http://localhost:8001"));
app.use(["/berry-mappemonde-expedition",
         "/expedition",
         "/health"],                         proxy("http://localhost:8002"));
app.use(["/assess-risks", "/assess-route"],  proxy("http://localhost:8003"));

// --- Static React build ---
const distPath = path.join(__dirname, "naviguide-app-main", "dist");
app.use(express.static(distPath));
// SPA fallback – compatible with Express 5 / path-to-regexp v8
app.use((_req, res) =>
  res.sendFile(path.join(distPath, "index.html"))
);

app.listen(PORT, "0.0.0.0", () =>
  console.log(`NAVIGUIDE proxy server running on port ${PORT}`)
);
