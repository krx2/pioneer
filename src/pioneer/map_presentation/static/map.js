// The map page: its markers in world coordinates, panned and zoomed through the viewBox (see
// map-view.js), resized with the zoom. `initialView`, `worldWidth`, `markerUnit`, `world` and
// `tiles` are set by the page before this runs.

const svg = document.getElementById("map");
let view = {...initialView};
const maxWidth = Math.max(initialView.w * 6, worldWidth * 1.2);
const markers = [...svg.querySelectorAll(".marker")].map(
  (group) => [group, Number(group.dataset.x), Number(group.dataset.y)]);
const tileLayer = tiles && world ? svg.querySelector(".world-tiles") : null;
const drawTiles = tileLayer ? mapTileLayer(tileLayer, tiles, world) : null;

function minWidth() {
  // As far in as the markers allow, but not past where the map picture stays sharp.
  const rect = svg.getBoundingClientRect();
  if (!tileLayer || !rect.width) return initialView.w * 0.04;
  return Math.max(initialView.w * 0.04,
    Math.min(tileMinWidth(tiles, world, rect.width), initialView.w));
}

function onScreen() {
  // The view box is fitted into the window ("meet"): one way, the screen shows more than it.
  const rect = svg.getBoundingClientRect();
  const scale = Math.max(view.w / rect.width, view.h / rect.height);
  const w = rect.width * scale;
  const h = rect.height * scale;
  return {x: view.x - (w - view.w) / 2, y: view.y - (h - view.h) / 2, w, h, pixels: rect.width};
}

function markerScale() {
  // On the game's map, markers are as big as a kilometre is long on screen allows -- the square
  // root of it, within bounds, as on the header's map; on a grid, they're sized for the first
  // view and grow by the square root of the zoom. Either way, close up they don't cover the map.
  const shown = onScreen();
  const perPixel = shown.w / shown.pixels;  // world units a screen pixel
  if (world) {
    const pixelsPerKm = 100000 / perPixel;
    const size = Math.min(Math.max(Math.sqrt(pixelsPerKm / 400), 0.6), 1.2);
    return (16 * size * perPixel) / markerUnit;  // a marker unit 16 screen pixels at full size
  }
  const zoom = initialView.w / view.w;
  return Math.min(Math.max(Math.sqrt(zoom), 0.5), 1.6) / zoom;
}

function applyView() {
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  const scale = markerScale();
  for (const [group, x, y] of markers) {
    group.setAttribute("transform", `translate(${x} ${y}) scale(${scale}) translate(${-x} ${-y})`);
  }
  if (drawTiles) {
    const shown = onScreen();
    drawTiles(shown, shown.pixels);
  }
}

panAndZoom(svg, {
  view: () => view,
  zoomStep: 1.15,
  bound: (width) => Math.min(Math.max(width, minWidth()), maxWidth),
  changed: applyView,
  reset: () => {
    view = {...initialView};
    applyView();
  },
});
new ResizeObserver(() => applyView()).observe(svg);
