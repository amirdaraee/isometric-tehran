/** Where the site and its data live. BASE_URL is "/" on a custom domain, "/isometric-tehran/" on the GitHub
 * project page https://amirdaraee.github.io/isometric-tehran/ (set VITE_BASE at build time). TILES_URL moves the tiles to another host (R2) for the full city. */
const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

/** A path inside public/ ("/data/places.json") as a URL that works under any base path. Full URLs pass through. */
export function asset(path: string): string {
  if (/^[a-z]+:\/\//i.test(path) || path.startsWith("data:")) return path;
  return `${BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

export const tilesBase = __TILES_URL__ ? `${__TILES_URL__}/tiles` : asset("/tiles");
