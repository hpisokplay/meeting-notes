// 產生純色圓角 App 圖示（PNG），無需外部套件。中央畫一個簡單的「聲波/麥克風」白色圖形。
import { writeFileSync, mkdirSync } from 'node:fs';
import zlib from 'node:zlib';

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
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
    raw[y * stride] = 0; // filter type none
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
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // color type RGB
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

const BLUE = [10, 132, 255];
const WHITE = [255, 255, 255];

function draw(x, y, size) {
  const cx = size / 2;
  const cy = size / 2;
  const nx = x / size;
  const ny = y / size;
  // 麥克風膠囊：中央直立圓角矩形
  const capW = size * 0.14;
  const capTop = size * 0.24;
  const capBot = size * 0.56;
  const inCapX = Math.abs(x - cx) <= capW;
  const inCapY = y >= capTop && y <= capBot;
  const capR = capW; // 圓角半徑（頂/底）
  let inCap = inCapX && inCapY;
  if (inCapY) {
    // 頂端與底端做圓角
    if (y < capTop + capR) {
      const dx = x - cx;
      const dy = y - (capTop + capR);
      inCap = inCapX && dx * dx + dy * dy <= capR * capR ? true : y >= capTop + capR ? inCapX : inCap;
    }
  }
  // 底座弧（U 形）
  const standR1 = size * 0.2;
  const standR2 = size * 0.24;
  const dxc = x - cx;
  const dyc = y - cy;
  const dist = Math.sqrt(dxc * dxc + dyc * dyc);
  const inArc = y > cy && dist >= standR1 && dist <= standR2;
  // 底座直桿
  const inStem = Math.abs(x - cx) <= size * 0.02 && y >= size * 0.72 && y <= size * 0.82;
  const inBase = Math.abs(x - cx) <= size * 0.12 && y >= size * 0.8 && y <= size * 0.83;

  if (inCap || inArc || inStem || inBase) return WHITE;
  return BLUE;
}

mkdirSync('icons', { recursive: true });
for (const s of [180, 192, 512]) {
  writeFileSync(`icons/icon-${s}.png`, png(s, draw));
}
console.log('icons written: 180, 192, 512');
