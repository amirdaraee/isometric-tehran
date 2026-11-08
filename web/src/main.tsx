import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/global.css";
import "./i18n"; // sets <html lang dir> before the first render
import { startAnalytics } from "./analytics";
import { startMonitoring } from "./monitoring";

// Error reports: listening from the start, the SDK loads after the map has drawn (see monitoring.ts).
startMonitoring();
// Statistics start once the map's first view has drawn (or after 8 s), not before it.
let started = false;
const startStats = () => { if (!started) { started = true; startAnalytics(); } };
addEventListener("map-drawn", startStats, { once: true });
setTimeout(startStats, 8000);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
