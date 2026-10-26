// What the map page and the header's Map panel share: the game's map drawn from its tiles under an
// <svg> in world coordinates, and panning and zooming that <svg> through its viewBox.

const MAP_SVG_NS = "http://www.w3.org/2000/svg";
const MAX_TILE_MAGNIFICATION = 4;  // screen pixels a pixel of the sharpest tiles may cover

// The map picture as tiles (see pioneer.map_image), in `layer`: `draw(shown, pixels)` puts in, for
// the part of the world `shown` across `pixels` of screen, every level up to the one sharp at that
// zoom -- the coarser ones show under it while it loads -- and takes out what's off screen.
function mapTileLayer(layer, tiles, world) {
  const levels = [];  // a group of images a level, the coarsest first: the sharper over it
  const images = new Map();  // "level/column_row" -> its image, on screen or not
  return function draw(shown, pixels) {
    const wanted = (world.w / shown.w) * pixels * (window.devicePixelRatio || 1);
    let sharp = tiles.levels.findIndex(([width]) => width >= wanted);
    if (sharp < 0) sharp = tiles.levels.length - 1;
    const keep = new Set();
    for (let level = 0; level <= sharp; level++) {
      if (!levels[level]) {
        levels[level] = layer.appendChild(document.createElementNS(MAP_SVG_NS, "g"));
      }
      const [width, height] = tiles.levels[level];
      const perX = world.w / width;  // world units a pixel of this level
      const perY = world.h / height;
      const span = (from, to, origin, per, count) => [
        Math.max(0, Math.floor((from - origin) / (per * tiles.size))),
        Math.min(count - 1, Math.floor((to - origin) / (per * tiles.size))),
      ];
      const [left, right] = span(shown.x, shown.x + shown.w, world.x, perX,
        Math.ceil(width / tiles.size));
      const [top, bottom] = span(shown.y, shown.y + shown.h, world.y, perY,
        Math.ceil(height / tiles.size));
      for (let column = left; column <= right; column++) {
        for (let row = top; row <= bottom; row++) {
          const key = `${level}/${column}_${row}`;
          keep.add(key);
          if (images.has(key)) continue;
          const across = Math.min(tiles.size + tiles.overlap, width - column * tiles.size);
          const down = Math.min(tiles.size + tiles.overlap, height - row * tiles.size);
          const image = document.createElementNS(MAP_SVG_NS, "image");
          image.setAttribute("href", `${tiles.url}/${key}.webp`);
          image.setAttribute("x", world.x + column * tiles.size * perX);
          image.setAttribute("y", world.y + row * tiles.size * perY);
          image.setAttribute("width", across * perX);
          image.setAttribute("height", down * perY);
          image.setAttribute("preserveAspectRatio", "none");
          image.dataset.level = level;
          images.set(key, image);
        }
      }
    }
    for (const [key, image] of images) {
      if (!keep.has(key)) image.remove();
      else if (!image.isConnected) levels[image.dataset.level].append(image);
    }
  };
}

// The narrowest view across `pixels` of screen that keeps the sharpest tiles sharp enough.
function tileMinWidth(tiles, world, pixels) {
  const sharpest = world.w / tiles.levels[tiles.levels.length - 1][0];
  return sharpest * pixels / MAX_TILE_MAGNIFICATION;
}

// Panning and zooming `svg`: the wheel zooms about the pointer by `zoomStep`, the width kept to
// what `bound(width)` allows; a press moved more than a few pixels drags the map, and the click it
// ends in is no click -- `dragged()` says so -- while one that doesn't is a click, on a marker
// say. A double click calls `reset`. `view()` is the part of the world shown, `{x, y, w, h}`, or
// nothing while there's none; `changed()` redraws after it moved.
function panAndZoom(svg, {view, zoomStep, bound, changed, reset}) {
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const shown = view();
    if (!shown) return;
    const rect = svg.getBoundingClientRect();
    const x = shown.x + (event.clientX - rect.left) * (shown.w / rect.width);
    const y = shown.y + (event.clientY - rect.top) * (shown.h / rect.height);
    const ratio = bound(shown.w * (event.deltaY > 0 ? zoomStep : 1 / zoomStep)) / shown.w;
    shown.x = x - (x - shown.x) * ratio;
    shown.y = y - (y - shown.y) * ratio;
    shown.w *= ratio;
    shown.h *= ratio;
    changed();
  }, {passive: false});

  // The pointer is captured only once the press is a drag, so that a click still lands on what it
  // was on. The browser's own dragging of an image (a tile, an icon) or selecting of text would
  // take the press over before it moves far enough to be the map's drag.
  let drag = null;
  let dragged = false;  // the last press was a drag
  svg.addEventListener("dragstart", (event) => event.preventDefault());
  svg.addEventListener("mousedown", (event) => {
    if (event.button === 0) event.preventDefault();
  });
  svg.addEventListener("pointerdown", (event) => {
    const shown = view();
    if (!shown || event.button !== 0) return;
    drag = {x: event.clientX, y: event.clientY, viewX: shown.x, viewY: shown.y, moved: 0};
    dragged = false;
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    if (!event.buttons) return endDrag();  // let go somewhere the map didn't hear of it
    drag.moved = Math.max(drag.moved, Math.hypot(event.clientX - drag.x, event.clientY - drag.y));
    if (drag.moved < 4) return;
    if (!svg.hasPointerCapture(event.pointerId)) svg.setPointerCapture(event.pointerId);
    svg.classList.add("dragging");
    const shown = view();
    const rect = svg.getBoundingClientRect();
    shown.x = drag.viewX - (event.clientX - drag.x) * (shown.w / rect.width);
    shown.y = drag.viewY - (event.clientY - drag.y) * (shown.h / rect.height);
    changed();
  });
  function endDrag() {
    dragged = Boolean(drag && drag.moved >= 4);
    drag = null;
    svg.classList.remove("dragging");
  }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("dblclick", reset);
  return {dragged: () => dragged};
}
