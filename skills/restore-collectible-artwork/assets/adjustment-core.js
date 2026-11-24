/* Deterministic picture-only controls. Shared by the offline UI and Node tests. */
(function (root) {
  'use strict';
  const limits = { brightness: 100, contrast: 100, saturation: 100, temperature: 100, hue: 180 };
  function settings(value = {}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('参数必须是对象');
    for (const key of Object.keys(value)) if (!Object.hasOwn(limits, key)) throw new Error(`未知参数：${key}`);
    const result = {};
    for (const [key, bound] of Object.entries(limits)) {
      const number = Object.hasOwn(value, key) ? value[key] : 0;
      if (typeof number !== 'number' || !Number.isFinite(number) || Math.abs(number) > bound)
        throw new Error(`${key} 必须在 ${-bound} 到 ${bound} 之间`);
      result[key] = number;
    }
    return result;
  }
  const neutral = value => Object.values(settings(value)).every(number => number === 0);
  function hueMatrix(degrees) {
    const angle = degrees * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
    return [
      [.213 + c * .787 - s * .213, .715 - c * .715 - s * .715, .072 - c * .072 + s * .928],
      [.213 - c * .213 + s * .143, .715 + c * .285 + s * .140, .072 - c * .072 - s * .283],
      [.213 - c * .213 - s * .787, .715 - c * .715 + s * .715, .072 + c * .928 + s * .072],
    ];
  }
  function adjust(source, values = {}) {
    const p = settings(values), result = new Uint8ClampedArray(source);
    if (source.length % 4) throw new Error('需要 RGBA 画面');
    if (neutral(p)) return result;
    const brightness = p.brightness / 100 * 46, contrast = 2 ** (p.contrast / 100);
    const saturation = 1 + p.saturation / 100, warmth = p.temperature / 100 * .22;
    const matrix = hueMatrix(p.hue);
    for (let i = 0; i < source.length; i += 4) {
      if (source[i + 3] === 0) continue;
      const r = source[i] * (1 + warmth), g = source[i + 1], b = source[i + 2] * (1 - warmth);
      const light = .2126 * r + .7152 * g + .0722 * b;
      let channels = [light + (r - light) * saturation, light + (g - light) * saturation, light + (b - light) * saturation];
      if (p.hue !== 0) channels = matrix.map(row => row[0] * channels[0] + row[1] * channels[1] + row[2] * channels[2]);
      for (let j = 0; j < 3; j++) result[i + j] = (channels[j] - 128) * contrast + 128 + brightness;
    }
    return result;
  }
  function fromBase64(text) { return Uint8Array.from(atob(text), character => character.charCodeAt(0)); }
  async function inflate(text) {
    const stream = new Blob([fromBase64(text)]).stream().pipeThrough(new DecompressionStream('deflate'));
    return new Uint8ClampedArray(await new Response(stream).arrayBuffer());
  }
  // Encode directly from RGBA, avoiding canvas premultiplied-alpha rounding at edges.
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
    return (crc ^ 0xffffffff) >>> 0;
  }
  function chunk(name, bytes) {
    const result = new Uint8Array(bytes.length + 12), view = new DataView(result.buffer);
    view.setUint32(0, bytes.length);
    result.set(new TextEncoder().encode(name), 4); result.set(bytes, 8);
    view.setUint32(bytes.length + 8, crc32(result.subarray(4, bytes.length + 8)));
    return result;
  }
  async function png(width, height, rgba) {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0 || rgba.length !== width * height * 4)
      throw new Error('画面尺寸与像素不一致');
    const rows = new Uint8Array((width * 4 + 1) * height);
    for (let y = 0; y < height; y++) rows.set(rgba.subarray(y * width * 4, (y + 1) * width * 4), y * (width * 4 + 1) + 1);
    const stream = new Blob([rows]).stream().pipeThrough(new CompressionStream('deflate'));
    const compressed = new Uint8Array(await new Response(stream).arrayBuffer());
    const header = new Uint8Array(13), view = new DataView(header.buffer);
    view.setUint32(0, width); view.setUint32(4, height); header[8] = 8; header[9] = 6;
    return new Blob([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', compressed), chunk('IEND', new Uint8Array())], { type: 'image/png' });
  }
  function exportPixels(master, values) { return adjust(master, values); }
  const api = { settings, neutral, adjust, hueMatrix, fromBase64, inflate, png, exportPixels };
  root.ArtworkColor = api;
  if (typeof module !== 'undefined') module.exports = api;
})(globalThis);
