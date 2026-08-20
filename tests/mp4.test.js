import { describe, it, expect } from 'vitest';
import { readM4aIndex, buildAdts, frameAtTime } from '../js/mp4.js';

// 手工組出一個最小的 m4a，用來驗證索引解析。
// 不放真實錄音檔進 repo：那會讓 repo 肥大，而且真實檔案的內容不受控。
function box(type, ...parts) {
  let len = 8;
  for (const p of parts) len += p.length;
  const out = new Uint8Array(len);
  const v = new DataView(out.buffer);
  v.setUint32(0, len);
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
  let o = 8;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}
const u8 = (...b) => new Uint8Array(b);
function u32(...vals) {
  const out = new Uint8Array(vals.length * 4);
  const v = new DataView(out.buffer);
  vals.forEach((x, i) => v.setUint32(i * 4, x));
  return out;
}
function u16(...vals) {
  const out = new Uint8Array(vals.length * 2);
  const v = new DataView(out.buffer);
  vals.forEach((x, i) => v.setUint16(i * 2, x));
  return out;
}
const zeros = (n) => new Uint8Array(n);

const TIMESCALE = 48000;
const FRAME_DELTA = 1024;
const SIZES = [10, 20, 30, 40, 50, 60];
const CHUNK_GAP = 7; // 兩個 chunk 之間留空隙，模擬真實錄音檔的寫入方式

// ASC：AAC-LC（objType 2）／48000Hz（freqIdx 3）／立體聲（chCfg 2）
const ASC = u8(0x11, 0x90);

function buildEsds() {
  const dsi = u8(0x05, ASC.length, ...ASC);
  const dcd = u8(0x04, 13 + dsi.length, 0x40, 0x15, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ...dsi);
  const es = u8(0x03, 3 + dcd.length, 0, 1, 0, ...dcd);
  return box('esds', u32(0), es);
}

function buildSampleFile() {
  const mp4a = box(
    'mp4a',
    zeros(6),
    u16(1), // data_reference_index
    zeros(8),
    u16(2), // channels
    u16(16), // sample size
    u16(0),
    u16(0),
    u32(TIMESCALE << 16 >>> 0), // 16.16 定點取樣率
    buildEsds()
  );
  const stsd = box('stsd', u32(0, 1), mp4a);
  const stts = box('stts', u32(0, 1), u32(SIZES.length, FRAME_DELTA));
  const stsc = box('stsc', u32(0, 1), u32(1, 3, 1)); // 每個 chunk 放 3 個音框
  const stsz = box('stsz', u32(0, 0, SIZES.length), u32(...SIZES));

  // 先用假位置佔位，等下面算出 mdat 真正的位置再回填
  const stcoPlaceholder = box('stco', u32(0, 2), u32(0, 0));
  const stbl = box('stbl', stsd, stts, stsc, stsz, stcoPlaceholder);
  const minf = box('minf', stbl);
  const mdhd = box('mdhd', u32(0, 0, 0, TIMESCALE, SIZES.length * FRAME_DELTA), u16(0x55c4, 0));
  const hdlr = box('hdlr', u32(0, 0), u8(0x73, 0x6f, 0x75, 0x6e), zeros(12), u8(0));
  const mdia = box('mdia', mdhd, hdlr, minf);
  const trak = box('trak', mdia);
  const moov = box('moov', trak);
  const ftyp = box('ftyp', u8(0x4d, 0x34, 0x41, 0x20), u32(0));

  const chunk0 = SIZES.slice(0, 3).reduce((a, b) => a + b, 0);
  const chunk1 = SIZES.slice(3).reduce((a, b) => a + b, 0);
  const mdatBody = new Uint8Array(chunk0 + CHUNK_GAP + chunk1);
  for (let i = 0; i < mdatBody.length; i++) mdatBody[i] = i & 0xff;
  const mdat = box('mdat', mdatBody);

  const mdatDataStart = ftyp.length + moov.length + 8;
  // 回填 stco：兩個 chunk 的真實檔案位置
  const stcoOffsetInMoov = moov.length - (stcoPlaceholder.length - 8) + 8 - 8;
  const v = new DataView(moov.buffer, moov.byteOffset, moov.byteLength);
  v.setUint32(stcoOffsetInMoov + 8, mdatDataStart);
  v.setUint32(stcoOffsetInMoov + 12, mdatDataStart + chunk0 + CHUNK_GAP);

  const file = new Uint8Array(ftyp.length + moov.length + mdat.length);
  file.set(ftyp, 0);
  file.set(moov, ftyp.length);
  file.set(mdat, ftyp.length + moov.length);
  return { blob: asFile(file), mdatDataStart, chunk0, bytes: file };
}

// jsdom 的 Blob.slice() 沒有 arrayBuffer()，這裡用最小替身提供解析器需要的介面。
// 真實 File 的行為由瀏覽器端的端對端測試涵蓋。
function asFile(bytes) {
  return {
    size: bytes.length,
    slice(a, b) {
      const part = bytes.slice(a, Math.min(b, bytes.length));
      return { arrayBuffer: async () => part.buffer.slice(part.byteOffset, part.byteOffset + part.byteLength) };
    },
  };
}

describe('m4a 索引解析', () => {
  it('讀得出取樣率、聲道、AAC 參數與音框數', async () => {
    const { blob } = buildSampleFile();
    const idx = await readM4aIndex(blob);
    expect(idx.timescale).toBe(TIMESCALE);
    expect(idx.sampleRate).toBe(TIMESCALE);
    expect(idx.channels).toBe(2);
    expect(idx.cfg).toEqual({ objType: 2, freqIdx: 3, chCfg: 2 });
    expect(idx.frameCount).toBe(SIZES.length);
    expect(idx.durationSec).toBeCloseTo((SIZES.length * FRAME_DELTA) / TIMESCALE, 6);
  });

  it('音框位置要跨過 chunk 之間的空隙，不能一路累加下去', async () => {
    const { blob, mdatDataStart, chunk0 } = buildSampleFile();
    const idx = await readM4aIndex(blob);
    expect([...idx.sizes]).toEqual(SIZES);
    expect(idx.offsets[0]).toBe(mdatDataStart);
    expect(idx.offsets[1]).toBe(mdatDataStart + 10);
    expect(idx.offsets[2]).toBe(mdatDataStart + 30);
    // 第 4 個音框是第二個 chunk 的開頭，位置要包含空隙
    expect(idx.offsets[3]).toBe(mdatDataStart + chunk0 + CHUNK_GAP);
    expect(idx.offsets[5]).toBe(mdatDataStart + chunk0 + CHUNK_GAP + 90);
  });

  it('每個音框的時間依序遞增', async () => {
    const { blob } = buildSampleFile();
    const idx = await readM4aIndex(blob);
    expect([...idx.times]).toEqual([0, 1024, 2048, 3072, 4096, 5120]);
    expect(frameAtTime(idx, 0)).toBe(0);
    expect(frameAtTime(idx, 3072 / TIMESCALE)).toBe(3);
    expect(frameAtTime(idx, 999)).toBe(idx.frameCount);
  });

  it('不是 MP4 的檔案要明確失敗，不能卡住', async () => {
    const junk = asFile(new Uint8Array(4096).fill(0x41));
    await expect(readM4aIndex(junk)).rejects.toThrow(/moov/);
  });
});

describe('組成 ADTS 串流', () => {
  it('每個音框前面加上 7 bytes 標頭，內容原封不動', async () => {
    const { blob, bytes } = buildSampleFile();
    const idx = await readM4aIndex(blob);
    const base = idx.offsets[0];
    const last = idx.offsets[2] + idx.sizes[2];
    const span = bytes.subarray(base, last);
    const adts = buildAdts(idx, 0, 3, span, base);

    expect(adts.length).toBe(3 * 7 + 10 + 20 + 30);
    let o = 0;
    for (let i = 0; i < 3; i++) {
      expect(adts[o]).toBe(0xff);
      expect(adts[o + 1] & 0xf0).toBe(0xf0); // sync word
      expect(adts[o + 1] & 0x01).toBe(1); // protection_absent，代表沒有 CRC
      expect(adts[o + 2] >> 6).toBe(1); // profile = objType - 1
      expect((adts[o + 2] >> 2) & 0x0f).toBe(3); // 取樣率索引
      const declared = ((adts[o + 3] & 0x03) << 11) | (adts[o + 4] << 3) | (adts[o + 5] >> 5);
      expect(declared).toBe(7 + SIZES[i]);
      // 標頭後面必須是原始音框位元組
      const src = bytes.subarray(idx.offsets[i], idx.offsets[i] + SIZES[i]);
      expect([...adts.subarray(o + 7, o + 7 + SIZES[i])]).toEqual([...src]);
      o += 7 + SIZES[i];
    }
  });

  it('聲道設定要寫進標頭（跨越第 3、4 bytes）', async () => {
    const { blob, bytes } = buildSampleFile();
    const idx = await readM4aIndex(blob);
    const adts = buildAdts(idx, 0, 1, bytes.subarray(idx.offsets[0], idx.offsets[0] + SIZES[0]), idx.offsets[0]);
    const chCfg = ((adts[2] & 0x01) << 2) | (adts[3] >> 6);
    expect(chCfg).toBe(2);
  });
});
