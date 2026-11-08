/** The city this viewer shows: city.json at the repository root, shared with the pipeline. */
import cfg from "../../city.json" with { type: "json" };

export interface Bilingual { en: string; fa: string }
export interface City {
  id: string;
  title: Bilingual;
  name: Bilingual;
  area: Bilingual;
  bbox: { west: number; south: number; east: number; north: number };
  metresPerPixel: number;
  tileSize: number;
  timezone: string;
  utcOffsetHours: number;
  openingPlace: string;
  openingZoom: number;
  repo: string;
  siteUrl: string;
  camera: { lookAzimuthDeg: number; elevationDeg: number };
}

export const city = cfg as City;
