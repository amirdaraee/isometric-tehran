import type { WheelEvent as ReactWheelEvent } from "react";

/** Labels and hit areas sit on layers above the map: a scroll over one still zooms the map, as anywhere else. */
export function forwardWheel(e: ReactWheelEvent) {
  const canvas = document.querySelector(".openseadragon-canvas");
  if (!canvas) return;
  const n = e.nativeEvent;
  canvas.dispatchEvent(new WheelEvent("wheel", {
    deltaX: n.deltaX, deltaY: n.deltaY, deltaZ: n.deltaZ, deltaMode: n.deltaMode,
    clientX: n.clientX, clientY: n.clientY, screenX: n.screenX, screenY: n.screenY,
    ctrlKey: n.ctrlKey, shiftKey: n.shiftKey, altKey: n.altKey, metaKey: n.metaKey,
    bubbles: true, cancelable: true,
  }));
}
