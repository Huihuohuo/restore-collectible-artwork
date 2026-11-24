/* Execute the real inline UI handlers with a small DOM adapter, without a browser. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const page = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const payload = JSON.parse(scripts.find(s => s[1].includes('application/json'))[2]);
const ids = [...page.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
const blobs = new Map(), downloads = [];
let painted;
function element(id) {
  const listeners = {};
  return { id, value: '0', dataset: {}, disabled: true, listeners, textContent: '',
    addEventListener(name, callback) { listeners[name] = callback; },
    setAttribute(name, value) { this[name] = value; },
    click() { if (this.href) downloads.push({ name: this.download, blob: blobs.get(this.href) }); },
    getContext() { return { putImageData(image) { painted = image.data; } }; },
  };
}
const elements = Object.fromEntries(ids.map(id => [id, element(id)]));
elements['artwork-data'].textContent = JSON.stringify(payload);
const sandbox = {
  console, Blob, Response, TextEncoder, CompressionStream, DecompressionStream, atob,
  Uint8Array, Uint8ClampedArray, DataView, ImageData: class { constructor(data) { this.data = data; } },
  URL: { createObjectURL(blob) { const key = `blob:${blobs.size}`; blobs.set(key, blob); return key; }, revokeObjectURL() {} },
  setTimeout(callback) { callback(); }, requestAnimationFrame(callback) { callback(); return 1; }, cancelAnimationFrame() {},
  document: {
    querySelector(selector) { return elements[selector.slice(1)]; },
    getElementById(id) { return elements[id]; },
    querySelectorAll() { return Object.values(elements); },
    createElement() { return element('download-link'); },
  },
};
vm.createContext(sandbox);
for (const script of scripts.filter(s => !s[1].includes('application/json'))) vm.runInContext(script[2], sandbox);
(async () => {
  for (let i = 0; i < 100 && !painted; i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(painted, 'Initial picture must render');
  const initial = Buffer.from(painted), core = sandbox.ArtworkColor;
  assert.ok(elements['download-initial'].href.startsWith('data:image/png;base64,'));
  elements.brightness.listeners.input({ target: { value: '35' } });
  const adjusted = Buffer.from(painted);
  assert.notDeepEqual(adjusted, initial);
  elements.compare.listeners.click();
  assert.deepEqual(Buffer.from(painted), initial, 'Compare must show the immutable initial');
  await elements.download.listeners.click();
  fs.writeFileSync(process.argv[3], Buffer.from(await downloads.at(-1).blob.arrayBuffer()));
  fs.writeFileSync(process.argv[4], adjusted);
  elements.reset.listeners.click();
  assert.deepEqual(Buffer.from(painted), initial, 'Reset must not start from adjusted pixels');
  await elements.download.listeners.click();
  assert.deepEqual(Buffer.from(await downloads.at(-1).blob.arrayBuffer()), Buffer.from(payload.png, 'base64'), 'Neutral download must preserve original file bytes');
  assert.throws(() => core.settings({ hue: null }));
  assert.throws(() => core.settings(JSON.parse('{"__proto__":0}')));
  console.log('UI handlers: initial, slider, compare/export, reset, neutral download passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
