/**
 * Visitor statistics, release build only.
 *  - Cloudflare Web Analytics: cookieless and anonymous, always on.
 *  - Google Analytics: sets cookies, so it loads only after the visitor accepts (the choice is kept in this
 *    browser and can be changed from About and credits).
 * IDs come from the build environment: VITE_CF_BEACON (Web Analytics site token) and VITE_GA_ID (G-…). None is set
 * in this project yet, so nothing loads and no consent banner is shown.
 */
const CF_TOKEN = import.meta.env.VITE_CF_BEACON as string | undefined;
const GA_ID = import.meta.env.VITE_GA_ID as string | undefined;
const KEY = "analytics-consent";
// Local previews send nothing (Cloudflare refuses other hosts anyway); add ?analytics to test them.
const ACTIVE = import.meta.env.PROD &&
  (!/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname) || new URLSearchParams(location.search).has("analytics"));

export type Consent = "yes" | "no" | null;

export const gaAvailable = ACTIVE && !!GA_ID;

export function readConsent(): Consent {
  try {
    const v = localStorage.getItem(KEY);
    return v === "yes" || v === "no" ? v : null;
  } catch {
    return null;
  }
}

export function saveConsent(c: "yes" | "no") {
  try { localStorage.setItem(KEY, c); } catch { /* private mode: the choice lasts this visit */ }
  if (c === "yes") loadGoogle();
  else if (GA_ID) {
    // Stop sending, and drop the cookies Google Analytics set on this site.
    (window as unknown as Record<string, boolean>)[`ga-disable-${GA_ID}`] = true;
    for (const c of document.cookie.split(";")) {
      const name = c.split("=")[0].trim();
      if (name === "_ga" || name.startsWith("_ga_")) {
        for (const domain of ["", `; domain=.${location.hostname}`]) document.cookie = `${name}=; Max-Age=0; path=/${domain}`;
      }
    }
  }
}

function addScript(src: string, attrs: Record<string, string> = {}) {
  const s = document.createElement("script");
  s.async = true;
  s.defer = true;
  s.src = src;
  for (const [k, v] of Object.entries(attrs)) s.setAttribute(k, v);
  document.head.appendChild(s);
}

let googleLoaded = false;
function loadGoogle() {
  if (!gaAvailable || googleLoaded) return;
  googleLoaded = true;
  (window as unknown as Record<string, boolean>)[`ga-disable-${GA_ID}`] = false;
  const w = window as unknown as { dataLayer: unknown[]; gtag: (...a: unknown[]) => void };
  w.dataLayer = w.dataLayer || [];
  w.gtag = function gtag() { w.dataLayer.push(arguments); }; // eslint-disable-line prefer-rest-params
  w.gtag("js", new Date());
  w.gtag("config", GA_ID, { anonymize_ip: true });
  addScript(`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`);
}

/** Called once at start-up. */
export function startAnalytics() {
  if (!ACTIVE) return;
  if (CF_TOKEN) addScript("https://static.cloudflareinsights.com/beacon.min.js", { "data-cf-beacon": JSON.stringify({ token: CF_TOKEN }) });
  if (readConsent() === "yes") loadGoogle();
}
