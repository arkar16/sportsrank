/* Drive Save image and inspect the marks and labels inside a center-cropped attachment. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const draws = [];
const ctx = {
  measureText(text) {
    return {width: parseInt(this.font.match(/\d+px/)[0]) * text.length * 0.55};
  },
  fillText(label, x, y, maxWidth) {
    const width = Math.min(maxWidth, this.measureText(label).width);
    const left = this.textAlign === "right" ? x - width : x;
    const height = parseInt(this.font.match(/\d+px/)[0]);
    draws.push({label, left, top: y - height / 2, right: left + width, bottom: y + height / 2});
  },
  drawImage(image, left, top, width, height) {
    draws.push({label: image.alt, left, top, right: left + width, bottom: top + height});
  },
  fillRect() {},
};

function team(name, rank, record) {
  const cells = {
    ".school": {textContent: name}, ".rank": {textContent: rank}, td: {textContent: record},
    img: {alt: name + " logo", naturalWidth: 500, naturalHeight: 500, decode: async () => {}},
  };
  return {querySelector: selector => cells[selector]};
}

async function checkDownload(bev) {
  draws.length = 0;
  const canvas = {getContext: () => ctx, toBlob: (done, type) => done({type})};
  const save = {addEventListener: (event, handler) => { save[event] = handler; }};
  const download = {click() { this.clicked = true; }, remove() {}};
  const selectors = {
    'meta[name="sportsrank:bev"]': bev === null ? null : {content: bev},
    "#game-link": {}, "#save-card": save, "#share-status": {},
    ".edition": {textContent: "CFB · 2026 · Week 13"},
    ".pick strong": {textContent: "Alabama by 17.75 · 89% to win", dataset: {}},
    ".game-details": {textContent: "Conference (SEC) · @ Alabama"},
  };
  const document = {
    querySelector: selector => selectors[selector],
    querySelectorAll: () => [team("Auburn", "#29", "7-4 (4-4)"), team("Alabama", "#3", "9-2 (6-2)")],
    createElement: tag => tag === "canvas" ? canvas : download,
    fonts: {load: async () => {}}, body: {append() {}},
  };
  const script = path.join(__dirname, "../website/cfb/assets/spread-card.js");
  vm.runInNewContext(fs.readFileSync(script, "utf8"), {
    document, navigator: {}, HTMLCanvasElement: {prototype: canvas}, location: {pathname: "/auburn-at-alabama.html"},
    URL: {createObjectURL(blob) { assert.equal(blob.type, "image/png"); return "blob:card"; }}, setTimeout() {},
  });
  await save.click();
  assert.ok(download.clicked);
  assert.equal(download.download, "auburn-at-alabama.png");
  const labels = draws.map(item => item.label);
  assert.deepEqual(labels.filter(label => label.startsWith("BEV ")), bev === null ? [] : ["BEV " + bev]);
  if (bev !== null) {
    const score = draws.find(item => item.label === "BEV " + bev);
    const venue = draws.find(item => item.label.startsWith("Conference (SEC)"));
    assert.ok(venue.right < score.left, "venue and BEV overlap");
  }
  for (const label of ["Auburn logo", "#29", "Auburn", "7-4 (4-4)", "Alabama logo", "#3", "Alabama",
                       "9-2 (6-2)", "Alabama by 17.75 · 89% to win", "Conference (SEC) · @ Alabama",
                       "CFB · 2026 · Week 13", "sportsrank.top"]) {
    assert.ok(labels.includes(label), label);
  }
  assert.ok(!labels.includes("CORS rank"));
  for (const aspect of [808 / 454, 3 / 2]) {
    const cropX = Math.max(0, (canvas.width - canvas.height * aspect) / 2);
    const cropY = Math.max(0, (canvas.height - canvas.width / aspect) / 2);
    for (const {label, left, top, right, bottom} of draws) {
      assert.ok(left >= cropX && right <= canvas.width - cropX &&
                top >= cropY && bottom <= canvas.height - cropY, label + " is cropped");
    }
  }
}

checkDownload("52.5").then(() => checkDownload("0.0")).then(() => checkDownload(null))
  .catch(error => { console.error(error); process.exitCode = 1; });
