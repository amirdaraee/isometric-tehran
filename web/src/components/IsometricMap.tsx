// Adapted from isometric-nyc by Andy Coenen (MIT, see LICENSE.isometric-nyc).
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import OpenSeadragon from "openseadragon";
import { tilesBase } from "../config";
import { gauge, perfOn, span } from "../perf";
import { markMoved } from "../frame";

export interface MapMeta {
  width: number;
  height: number;
  tileSize: number;
  mpp: number;
  art: "preview" | "painted" | "final";
  painted?: { file: string; x0: number; y0: number; w: number; h: number }[];
  /** Painted versions of the map (night, golden, seasons): variant -> tiles folder name under tilesBase. */
  variants?: Record<string, string>;
}

export interface View {
  x: number; // image pixels
  y: number;
  z: number; // screen pixels per image pixel
}

export interface MapHandle {
  flyTo: (x: number, y: number, z?: number) => void;
  zoomBy: (factor: number) => void;
  getView: () => View | null;
  toScreen: (x: number, y: number) => { x: number; y: number } | null;
  /** Inverse of toScreen: a point on the screen (CSS px) to map pixels. */
  toMap: (screenX: number, screenY: number) => { x: number; y: number } | null;
  /** Turn mouse and touch navigation (pan, zoom) off and back on. */
  setPanEnabled: (on: boolean) => void;
  /** Show painted versions on top of the day map with these opacities (0 hides). */
  setLayers: (alpha: Record<string, number>) => void;
  /** Small painted patches (events) placed in map px, with opacities. */
  setPatches: (patches: { url: string; x0: number; y0: number; w: number; a: number }[]) => void;
  /** Call `fn` in the same frame as every map redraw (before React hears about the move). Returns an unsubscribe. */
  onFrame: (fn: () => void) => () => void;
}

interface Props {
  meta: MapMeta;
  initial: View;
  /** Accessible name of the map region. */
  label: string;
  onViewChange: (v: View) => void;
  onReady: () => void;
  /** Once, when the first view of the map has fully drawn (or after a few seconds on a slow line). */
  onDrawn?: () => void;
}

export const IsometricMap = forwardRef<MapHandle, Props>(function IsometricMap({ meta, initial, label, onViewChange, onReady, onDrawn }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<OpenSeadragon.Viewer | null>(null);
  const layers = useRef<Record<string, OpenSeadragon.TiledImage | "loading">>({});
  const pending = useRef<Record<string, number>>({});
  const patches = useRef<Record<string, OpenSeadragon.TiledImage | "loading">>({});
  const patchAlpha = useRef<Record<string, number>>({});
  const frameFns = useRef(new Set<() => void>());
  const tileSourceFor = (folder: string) => ({
    width: meta.width, height: meta.height, tileSize: meta.tileSize, tileOverlap: 0, minLevel: 9,
    maxLevel: Math.ceil(Math.log2(Math.max(meta.width, meta.height))),
    getTileUrl: (level: number, x: number, y: number) => `${tilesBase}/${folder}/${level}/${x}_${y}.webp`,
  });

  // With extra layers on the map, convert through the base (day) image, not the viewport.
  const base = () => viewer.current?.world.getItemAt(0) as OpenSeadragon.TiledImage | undefined;

  // Painted versions are stacked by meaning: the base day map, then the season versions, golden hour, the night
  // versions, and the event patches on top.
  const rank = useRef(new Map<OpenSeadragon.TiledImage, number>());
  const loadedOnce = useRef(new Set<string>());
  const zeroSince = useRef<Record<string, number>>({});
  /** Start-up mark for the timing harness: the first time a visible map version has its whole view loaded. */
  const drawnSent = useRef(false);
  const sendDrawn = () => { if (!drawnSent.current) { drawnSent.current = true; onDrawn?.(); } };
  const markLoaded = (it: OpenSeadragon.TiledImage) => {
    if (it.getOpacity() > 0 && !performance.getEntriesByName("map-loaded").length) performance.mark("map-loaded");
    if (it.getOpacity() > 0) sendDrawn();
  };
  /** The version covering the ones under it at the last applyLayers, and since when it has been held (see below). */
  const covering = useRef<{ name: string; rank: number } | null>(null);
  const holdSince = useRef<number | null>(null);
  const holdTimer = useRef<ReturnType<typeof setTimeout>>();
  const rankOf = (name: string) => (name === "golden" ? 2 : name.includes("night") ? 3 : 1);

  /**
   * Sets each version's opacity. A version under one that is fully opaque and has loaded its first view is hidden
   * (opacity 0), so OpenSeadragon neither loads nor draws its tiles: at 21:00 in autumn that leaves only the night
   * map, not the day, autumn and night maps on top of each other. The base day map is hidden the same way, but
   * stays in the world, since every conversion between screen and map goes through it. A version that has been
   * at 0 for 30 s is removed (it is added again when needed).
   * Until the first view has loaded, nothing is on screen to keep, so a version counts as covering as soon as it
   * is asked for: the versions under it are never fetched at all.
   * Uncovering (night back to dusk on the slider): the versions under the cover loaded nothing while hidden, so the
   * covering version is held fully opaque while they load the view (at most 2 s), instead of fading it over empty
   * tiles (a dark flash).
   */
  const applyLayers = () => {
    const v = viewer.current, b = base();
    if (!v || !b) return;
    const now = performance.now();
    const starting = !performance.getEntriesByName("map-loaded").length;
    let cover = 0, coverName = ""; // rank under which everything is hidden, and the version at that rank
    for (const [name, it] of Object.entries(layers.current)) {
      if ((pending.current[name] ?? 0) >= 0.999 && (starting || (it !== "loading" && loadedOnce.current.has(name))) && rankOf(name) > cover) {
        cover = rankOf(name); coverName = name;
      }
    }
    let held: { name: string; rank: number } | null = null;
    const was = covering.current;
    if (was && cover < was.rank && !starting && (pending.current[was.name] ?? 0) > 0.001 && layers.current[was.name] !== undefined
      && layers.current[was.name] !== "loading") {
      // the versions about to show (their fully-loaded flag is only fresh once they have been preloading a moment)
      const under: OpenSeadragon.TiledImage[] = cover === 0 ? [b] : [];
      for (const [name, it] of Object.entries(layers.current)) {
        if (it !== "loading" && rankOf(name) < was.rank && rankOf(name) >= cover && (pending.current[name] ?? 0) > 0.001) under.push(it);
      }
      holdSince.current ??= now;
      const loaded = now - holdSince.current > 120 && under.every((it) => it.getFullyLoaded());
      if (!loaded && now - holdSince.current < 2000) {
        held = was;
        for (const it of under) it.setPreload(true);
        if (!holdTimer.current) holdTimer.current = setTimeout(() => { holdTimer.current = undefined; applyLayers(); }, 100);
      }
    }
    if (!held) holdSince.current = null;
    const shown = held ? held.rank : cover;
    covering.current = held ?? (cover > 0 ? { name: coverName, rank: cover } : null);
    b.setOpacity(shown > 0 ? 0 : 1);
    if (!held) b.setPreload(false);
    for (const [name, it] of Object.entries(layers.current)) {
      if (it === "loading") continue;
      const a = pending.current[name] ?? 0;
      it.setOpacity(rankOf(name) < shown ? 0 : held && name === held.name ? 1 : a);
      if (!held) it.setPreload(false);
      if (a > 0.001) { delete zeroSince.current[name]; continue; }
      zeroSince.current[name] ??= now;
      if (now - zeroSince.current[name] > 30000) {
        v.world.removeItem(it);
        rank.current.delete(it);
        delete layers.current[name];
        delete zeroSince.current[name];
        loadedOnce.current.delete(name);
      }
    }
  };

  useImperativeHandle(ref, () => ({
    flyTo(x, y, z) {
      const v = viewer.current;
      if (!v) return;
      const vp = v.viewport;
      const b = base();
      if (!b) return;
      vp.panTo(b.imageToViewportCoordinates(new OpenSeadragon.Point(x, y)));
      if (z !== undefined) vp.zoomTo(b.imageToViewportZoom(z));
    },
    zoomBy(factor) {
      const vp = viewer.current?.viewport;
      if (vp) vp.zoomBy(factor);
    },
    getView() {
      const vp = viewer.current?.viewport;
      const b = base();
      if (!vp || !b) return null;
      const c = b.viewportToImageCoordinates(vp.getCenter(true));
      return { x: c.x, y: c.y, z: b.viewportToImageZoom(vp.getZoom(true)) };
    },
    setLayers(alpha) {
      const v = viewer.current;
      if (!v) return;
      for (const [name, a] of Object.entries(alpha)) {
        const folder = meta.variants?.[name];
        if (!folder) continue;
        const item = layers.current[name];
        if (!item && a > 0.001) {
          // added lazily, the first time a version is needed
          layers.current[name] = "loading";
          v.addTiledImage({
            tileSource: tileSourceFor(folder),
            opacity: 0,
            success: (e: { item: OpenSeadragon.TiledImage }) => {
              layers.current[name] = e.item;
              rank.current.set(e.item, rankOf(name));
              // in its place by meaning, not by the order versions were first needed; event patches stay on top
              let at = 0;
              for (let i = 0; i < v.world.getItemCount(); i++) {
                const it = v.world.getItemAt(i);
                if (it !== e.item && (rank.current.get(it) ?? 0) <= rankOf(name)) at++;
              }
              v.world.setItemIndex(e.item, Math.min(at, v.world.getItemCount() - 1));
              e.item.addHandler("fully-loaded-change", (ev: { fullyLoaded: boolean }) => {
                if (!ev.fullyLoaded) return;
                markLoaded(e.item);
                if (!loadedOnce.current.has(name)) { loadedOnce.current.add(name); applyLayers(); }
              });
              applyLayers();
            },
          } as unknown as OpenSeadragon.TiledImageOptions);
        }
        pending.current[name] = a;
      }
      applyLayers();
    },
    setPatches(list) {
      const v = viewer.current;
      if (!v) return;
      for (const p of list) {
        const item = patches.current[p.url];
        if (!item && p.a > 0.001) {
          patches.current[p.url] = "loading";
          v.addSimpleImage({
            url: p.url, x: p.x0 / meta.width, y: p.y0 / meta.width, width: p.w / meta.width,
            success: (e: { item: OpenSeadragon.TiledImage }) => {
              patches.current[p.url] = e.item; rank.current.set(e.item, 99); e.item.setOpacity(patchAlpha.current[p.url] ?? p.a);
            },
            error: () => { delete patches.current[p.url]; },
          } as unknown as OpenSeadragon.SimpleImageOptions);
        } else if (item && item !== "loading") (item as OpenSeadragon.TiledImage).setOpacity(p.a);
        patchAlpha.current[p.url] = p.a;
      }
    },
    toScreen(x, y) {
      const vp = viewer.current?.viewport;
      const b = base();
      if (!vp || !b) return null;
      const p = vp.pixelFromPoint(b.imageToViewportCoordinates(new OpenSeadragon.Point(x, y)), true);
      return { x: p.x, y: p.y };
    },
    toMap(sx, sy) {
      const vp = viewer.current?.viewport;
      const b = base();
      if (!vp || !b) return null;
      const p = b.viewportToImageCoordinates(vp.pointFromPixel(new OpenSeadragon.Point(sx, sy), true));
      return { x: p.x, y: p.y };
    },
    setPanEnabled(on) {
      viewer.current?.setMouseNavEnabled(on);
    },
    onFrame(fn) {
      frameFns.current.add(fn);
      return () => { frameFns.current.delete(fn); };
    },
  }));

  useEffect(() => {
    if (!host.current || viewer.current) return;
    const { width, height, tileSize, art } = meta;
    const osd = OpenSeadragon as unknown as { isCanvasTainted: (c: HTMLCanvasElement) => boolean; getCurrentPixelDensityRatio: () => number; pixelDensityRatio: number };
    // OpenSeadragon's WebGL drawer tests every new tile for cross-origin taint by reading a pixel back from a 2D
    // canvas, which stalls on the GPU each time: two thirds of the main thread while panning.
    // Every image here is loaded with crossOrigin "anonymous" (crossOriginPolicy below), so an image without CORS
    // fails to load instead of tainting: no tile can be tainted, and the test is skipped.
    osd.isCanvasTainted = () => false;
    // At most two device pixels per CSS pixel for the map's canvas: a phone at 3 draws 2.25 times the pixels for
    // a difference nobody sees in painted art.
    const density = osd.getCurrentPixelDensityRatio.bind(osd);
    osd.getCurrentPixelDensityRatio = () => Math.min(density(), 2);
    osd.pixelDensityRatio = osd.getCurrentPixelDensityRatio();
    const v = OpenSeadragon({
      element: host.current,
      prefixUrl: "",
      showNavigationControl: false,
      crossOriginPolicy: "Anonymous",
      showNavigator: window.innerWidth > 640,
      navigatorPosition: "TOP_RIGHT",
      navigatorSizeRatio: 0.14,
      navigatorMaintainSizeRatio: true,
      navigatorAutoFade: false,
      navigatorBackground: "#101828",
      navigatorBorderColor: "rgba(255,255,255,.2)",
      navigatorDisplayRegionColor: "#ef3340",
      animationTime: 0.45,
      springStiffness: 9,
      blendTime: 0.1,
      immediateRender: false,
      minZoomImageRatio: 0.8,
      // Never zoom much past the map's own detail (city.json metresPerPixel).
      maxZoomPixelRatio: 1.25,
      visibilityRatio: 0.35,
      constrainDuringPan: true,
      preserveImageSizeOnResize: true,
      placeholderFillStyle: "#172033",
      // Each cached tile holds its image, a 1 MB canvas and a 1 MB texture: fewer on phones than the default 200.
      maxImageCacheCount: matchMedia("(pointer: coarse)").matches ? 100 : 200,
      gestureSettingsMouse: { scrollToZoom: true, clickToZoom: false, dblClickToZoom: true, flickEnabled: true },
      gestureSettingsTouch: { pinchToZoom: true, clickToZoom: false, dblClickToZoom: true, flickEnabled: true },
      // Crisp pixels suit the final pixel art; the 3D preview reads better smoothed.
      imageSmoothingEnabled: art !== "final",
      tileSources: {
        width,
        height,
        tileSize,
        tileOverlap: 0,
        minLevel: 9,
        maxLevel: Math.ceil(Math.log2(Math.max(width, height))),
        getTileUrl: (level: number, x: number, y: number) => `${tilesBase}/map_files/${level}/${x}_${y}.webp`,
      },
    } as OpenSeadragon.Options);

    v.addHandler("open", () => {
      const vp = v.viewport;
      const b = v.world.getItemAt(0);
      // start-up marks for the timing harness: the first tile loaded, and the whole first view loaded
      performance.mark("map-open");
      v.addOnceHandler("tile-loaded", () => { performance.mark("map-first-tile"); });
      b.addHandler("fully-loaded-change", (e: { fullyLoaded: boolean }) => { if (e.fullyLoaded) markLoaded(b); });
      // The base map waits, hidden, for the first setLayers (or a moment), in case a version will cover it.
      b.setOpacity(0);
      setTimeout(applyLayers, 250);
      setTimeout(sendDrawn, 6000);   // a slow line: don't hold the rest back for ever
      vp.zoomTo(b.imageToViewportZoom(initial.z), undefined, true);
      vp.panTo(b.imageToViewportCoordinates(new OpenSeadragon.Point(initial.x, initial.y)), true);
      onReady();
    });
    const report = () => {
      const vp = v.viewport;
      const b = v.world.getItemAt(0);
      if (!vp || !b) return;
      const c = b.viewportToImageCoordinates(vp.getCenter(true));
      onViewChange({ x: c.x, y: c.y, z: b.viewportToImageZoom(vp.getZoom(true)) });
    };
    // Things pinned to the map (labels) move here, in the same frame as the map is drawn; React only decides
    // which of them show.
    v.addHandler("update-viewport", () => {
      const end = span("mapFrame");
      markMoved();
      for (const fn of frameFns.current) fn();
      report();
      end();
    });
    viewer.current = v;
    // removes versions that have stood at 0 for 30 s, even when nothing else changes
    const sweep = setInterval(applyLayers, 10000);
    let unGauges: (() => void)[] = [];
    if (perfOn) {
      // The map's own drawing, the versions it holds and its tile cache, for the readout and the harness.
      const world = v.world as unknown as { draw: () => void };
      const drawWorld = world.draw.bind(world);
      world.draw = () => { const end = span("map"); drawWorld(); end(); };
      unGauges = [
        gauge("osdItems", () => v.world.getItemCount()),
        gauge("tilesCached", () => (v as unknown as { tileCache: { numTilesLoaded: () => number } }).tileCache.numTilesLoaded()),
      ];
      (window as unknown as { __iso: unknown }).__iso = { viewer: v, OpenSeadragon, drawer: () => (v.drawer as unknown as { getType?: () => string })?.getType?.() };
    }
    return () => {
      unGauges.forEach((f) => f());
      clearInterval(sweep);
      clearTimeout(holdTimer.current);
      v.destroy();
      viewer.current = null;
    };
    // The viewer lives for the whole page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={host} className="map" role="region" aria-label={label} />;
});
