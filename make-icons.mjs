// 產生漸層質感的 App 圖示（藍→靛漸層 + 白色麥克風），無需外部套件。
import { writeFileSync, mkdirSync } from 'node:fs';
import zlib from 'node:zlib';

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return (~c) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size, draw) {
  const bpp = 3;
  const stride = size * bpp + 1;
  const raw = Buffer.alloc(stride * size);
  for (let y = 0; y < size; y++) {
    raw[y * stride] = 0;
    for (let x = 0; x < size; x++) {
      const [r, g, b] = draw(x, y, size);
      const o = y * stride + 1 + x * bpp;
      raw[o] = r;
      raw[o + 1] = g;
      raw[o + 2] = b;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

const C1 = [10, 132, 255]; // #0A84FF
const C2 = [94, 92, 230]; // #5E5CE6
const WHITE = [255, 255, 255];

function draw(x, y, size) {
  const cx = size / 2;
  // 麥克風膠囊（stadium）：上下半圓 + 中間直條
  const capR = size * 0.088;
  const topC = size * 0.24;
  const botC = size * 0.46;
  const dxc = x - cx;
  const inBody = Math.abs(dxc) <= capR && y >= topC && y <= botC;
  const inTop = Math.hypot(dxc, y - topC) <= capR;
  const inBot = Math.hypot(dxc, y - botC) <= capR;
  const inCapsule = inBody || inTop || inBot;

  // 底座弧（U 形）
  const arcCy = size * 0.46;
  const dr = Math.hypot(x - cx, y - arcCy);
  const inArc = y > arcCy && dr >= size * 0.185 && dr <= size * 0.225;
  // 直桿與底座
  const inStem = Math.abs(dxc) <= size * 0.016 && y >= size * 0.685 && y <= size * 0.78;
  const inBase = Math.abs(dxc) <= size * 0.1 && y >= size * 0.78 && y <= size * 0.807;

  if (inCapsule || inArc || inStem || inBase) return WHITE;

  // 對角漸層背景
  const t = (x + y) / (2 * size);
  return [
    Math.round(C1[0] + (C2[0] - C1[0]) * t),
    Math.round(C1[1] + (C2[1] - C1[1]) * t),
    Math.round(C1[2] + (C2[2] - C1[2]) * t),
  ];
}

mkdirSync('icons', { recursive: true });
for (const s of [180, 192, 512]) {
  writeFileSync(`icons/icon-${s}.png`, png(s, draw));
}
console.log('icons written: 180, 192, 512');
