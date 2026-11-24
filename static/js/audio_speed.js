/* Ubah Kecepatan Audio — tempo change that keeps the pitch.
 *
 * WAV files (and every file when the server has no FFmpeg) are processed in
 * the browser with WSOLA (waveform-similarity overlap-add), the same family of
 * algorithm as FFmpeg's `atempo`. Other formats go to /media/audio-speed,
 * which runs FFmpeg `atempo` so the original format is kept.
 */
(() => {
  const MIN_SPEED = 0.5, MAX_SPEED = 2.0;

  // ── Core DSP (no DOM; also loadable from Node for tests) ──────────────

  function parseWavInfo(buf) {
    const v = new DataView(buf);
    const tag = o => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
    if (buf.byteLength < 12 || tag(0) !== 'RIFF' || tag(8) !== 'WAVE') return null;
    for (let o = 12; o + 8 <= buf.byteLength;) {
      const size = v.getUint32(o + 4, true);
      if (tag(o) === 'fmt ' && o + 24 <= buf.byteLength) {
        return {channels: v.getUint16(o + 10, true), sampleRate: v.getUint32(o + 12, true), bits: v.getUint16(o + 22, true)};
      }
      o += 8 + size + (size & 1);
    }
    return null;
  }

  function parseMp3Info(buf) {
    const b = new Uint8Array(buf);
    let i = 0;
    if (b.length > 10 && b[0] === 0x49 && b[1] === 0x44 && b[2] === 0x33) {  // "ID3"
      i = 10 + ((b[6] & 0x7f) << 21 | (b[7] & 0x7f) << 14 | (b[8] & 0x7f) << 7 | (b[9] & 0x7f)) + (b[5] & 0x10 ? 10 : 0);
    }
    for (const end = Math.min(b.length - 4, i + 65536); i < end; i++) {
      if (b[i] !== 0xff || (b[i + 1] & 0xe0) !== 0xe0) continue;
      const version = (b[i + 1] >> 3) & 3, layer = (b[i + 1] >> 1) & 3;
      const bitrate = b[i + 2] >> 4, rateIdx = (b[i + 2] >> 2) & 3;
      if (version === 1 || layer === 0 || bitrate === 0 || bitrate === 15 || rateIdx === 3) continue;
      const base = [44100, 48000, 32000][rateIdx];
      return {sampleRate: version === 3 ? base : version === 2 ? base / 2 : base / 4, channels: (b[i + 3] >> 6) === 3 ? 1 : 2};
    }
    return null;
  }

  /** Time-stretch `channels` (Float32Array[]) by `speed` without changing pitch. */
  async function wsola(channels, sampleRate, speed, onProgress) {
    const n = channels[0].length;
    if (Math.abs(speed - 1) < 1e-6) return channels.map(c => c.slice());
    const L = 2 * Math.round(0.02 * sampleRate);          // 40 ms frame
    const Hs = L / 2;                                      // synthesis hop (50% overlap)
    const Ha = Hs * speed;                                 // analysis hop
    const tol = Math.round(0.012 * sampleRate);            // ±12 ms search window
    const d = Math.max(1, Math.floor(sampleRate / 8000));  // decimation for the coarse search
    const P = 2 * L + tol + d;                             // zero padding on both sides
    const outLen = Math.round(n / speed);

    const padded = channels.map(c => { const p = new Float32Array(n + 2 * P + 2 * L); p.set(c, P); return p; });
    const mono = new Float32Array(padded[0].length);
    for (const p of padded) for (let i = 0; i < mono.length; i++) mono[i] += p[i] / padded.length;
    const dec = new Float32Array(Math.floor(mono.length / d));
    for (let j = 0; j < dec.length; j++) { let s = 0; for (let k = 0; k < d; k++) s += mono[j * d + k]; dec[j] = s / d; }

    const win = new Float32Array(L);
    for (let i = 0; i < L; i++) win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / L);
    const out = channels.map(() => new Float32Array(outLen + 2 * L));
    const wsum = new Float32Array(outLen + 2 * L);
    const frames = Math.ceil(outLen / Hs) + 2;
    const maxStart = padded[0].length - L - tol - d - 1;
    const clampStart = x => Math.min(maxStart, Math.max(tol + d, x));

    let delta = 0;
    for (let k = 0; k < frames; k++) {
      const inStart = clampStart(P + Math.round(k * Ha) - Hs + delta);
      const outStart = k * Hs - Hs + L;                    // shifted by L; trimmed below
      for (let c = 0; c < padded.length; c++) {
        const src = padded[c], dst = out[c];
        for (let i = 0; i < L; i++) { const o = outStart + i; if (o >= 0 && o < dst.length) dst[o] += src[inStart + i] * win[i]; }
      }
      for (let i = 0; i < L; i++) { const o = outStart + i; if (o >= 0 && o < wsum.length) wsum[o] += win[i]; }

      // Pick the next frame so it continues this one's waveform most smoothly.
      const natural = inStart + Hs;
      const nominal = P + Math.round((k + 1) * Ha) - Hs;
      const nd = Math.floor(natural / d), Ld = Math.floor(L / d);
      let best = 0, bestScore = -Infinity;
      for (let off = -tol; off <= tol; off += d) {
        const cand = Math.floor(clampStart(nominal + off) / d);
        let dot = 0, energy = 1e-9;
        for (let i = 0; i < Ld; i++) { const x = dec[cand + i]; dot += x * dec[nd + i]; energy += x * x; }
        const score = dot / Math.sqrt(energy);
        if (score > bestScore) { bestScore = score; best = off; }
      }
      let fine = best; bestScore = -Infinity;
      for (let off = best - d; off <= best + d; off++) {
        if (off < -tol || off > tol) continue;
        const cand = clampStart(nominal + off);
        let dot = 0, energy = 1e-9;
        for (let i = 0; i < L; i += 2) { const x = mono[cand + i]; dot += x * mono[natural + i]; energy += x * x; }
        const score = dot / Math.sqrt(energy);
        if (score > bestScore) { bestScore = score; fine = off; }
      }
      delta = fine;

      if (onProgress && k % 400 === 0) { onProgress(k / frames); await new Promise(r => setTimeout(r)); }
    }
    if (onProgress) onProgress(1);
    return out.map(o => {
      const res = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) { const w = wsum[i + L]; res[i] = w > 1e-3 ? o[i + L] / w : 0; }
      return res;
    });
  }

  function encodeWav(channels, sampleRate) {
    const ch = channels.length, n = channels[0].length, bytes = n * ch * 2;
    const buffer = new ArrayBuffer(44 + bytes), v = new DataView(buffer);
    const str = (o, s) => { for (let i = 0; i < 4; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + bytes, true); str(8, 'WAVE'); str(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, ch, true);
    v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * ch * 2, true);
    v.setUint16(32, ch * 2, true); v.setUint16(34, 16, true); str(36, 'data'); v.setUint32(40, bytes, true);
    let o = 44;
    for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) {
      const s = Math.max(-1, Math.min(1, channels[c][i]));
      v.setInt16(o, Math.round(s < 0 ? s * 32768 : s * 32767), true); o += 2;
    }
    return buffer;
  }

  const core = {wsola, encodeWav, parseWavInfo, parseMp3Info, MIN_SPEED, MAX_SPEED};
  if (typeof module !== 'undefined' && module.exports) module.exports = core;
  if (typeof document === 'undefined') return;
  window.AccaAudioSpeed = core;

  // ── UI ─────────────────────────────────────────────────────────────────

  const $ = id => document.getElementById(id);
  const tool = $('speed-tool');
  if (!tool) return;
  const serverFfmpeg = tool.dataset.ffmpeg === '1';
  const range = $('speed-range'), number = $('speed-number'), run = $('speed-run');
  const status = $('speed-status'), errorBox = $('speed-error');
  let file = null, fileBuf = null, decoded = null, isWav = false, resultUrl = null, busy = false;

  const comma = (x, digits) => x.toFixed(digits).replace('.', ',');
  function fmtTime(sec) {
    if (!isFinite(sec)) return '–';
    const m = Math.floor(sec / 60), s = sec - m * 60;
    return `${m}:${s < 10 ? '0' : ''}${comma(s, 1)}`;
  }
  function fmtSize(bytes) { return bytes >= 1048576 ? `${comma(bytes / 1048576, 1)} MB` : `${Math.round(bytes / 1024)} KB`; }
  function speedLabel(s) { return s.toFixed(3).replace(/\.?0+$/, ''); }
  function showError(msg) { errorBox.textContent = msg; errorBox.hidden = !msg; }
  function getSpeed() {
    const s = parseFloat(String(number.value).replace(',', '.'));
    return isFinite(s) ? Math.min(MAX_SPEED, Math.max(MIN_SPEED, s)) : 1;
  }
  function useServer() { return serverFfmpeg && !isWav; }

  function refresh() {
    const s = getSpeed();
    $('speed-label').textContent = `${comma(s, 2)}×`;
    document.querySelectorAll('.speed-chips [data-speed]').forEach(b => b.classList.toggle('active', Math.abs(parseFloat(b.dataset.speed) - s) < 1e-6));
    if (decoded) {
      $('speed-original').textContent = fmtTime(decoded.duration);
      $('speed-estimate').textContent = fmtTime(decoded.duration / s);
    }
    run.disabled = busy || !file || (!decoded && !useServer());
  }
  function setSpeed(s, from) {
    s = Math.min(MAX_SPEED, Math.max(MIN_SPEED, s));
    if (from !== range) range.value = String(s);
    if (from !== number) number.value = String(Math.round(s * 1000) / 1000);
    refresh();
  }
  range.addEventListener('input', () => setSpeed(parseFloat(range.value), range));
  number.addEventListener('input', () => { const s = parseFloat(String(number.value).replace(',', '.')); if (isFinite(s)) setSpeed(s, number); });
  number.addEventListener('change', () => setSpeed(getSpeed()));
  document.querySelectorAll('.speed-chips [data-speed]').forEach(b => b.addEventListener('click', () => setSpeed(parseFloat(b.dataset.speed))));

  $('speed-target-apply').addEventListener('click', () => {
    showError('');
    if (!decoded) { showError('Pilih file audio dulu.'); return; }
    const raw = $('speed-target').value.trim().replace(',', '.');
    const parts = raw.split(':').map(Number);
    const target = parts.length === 2 ? parts[0] * 60 + parts[1] : parts.length === 1 ? parts[0] : NaN;
    if (!isFinite(target) || target <= 0) { showError('Format durasi target tidak valid. Contoh: 2:30 atau 150.'); return; }
    const s = decoded.duration / target;
    if (s < MIN_SPEED || s > MAX_SPEED) {
      showError(`Durasi target membutuhkan kecepatan ${comma(s, 2)}×, di luar batas 0,5×–2,0×.`);
      return;
    }
    setSpeed(Math.round(s * 1000) / 1000);
  });

  async function loadFile(f) {
    showError(''); status.textContent = ''; $('speed-result').hidden = true;
    file = f; decoded = null; fileBuf = null;
    if (!f) { refresh(); return; }
    $('speed-file-name').textContent = f.name;
    status.textContent = 'Membaca audio…';
    busy = true; refresh();
    try {
      fileBuf = await f.arrayBuffer();
      const wavInfo = parseWavInfo(fileBuf);
      isWav = !!wavInfo;
      const info = wavInfo || parseMp3Info(fileBuf) || {sampleRate: 48000};
      const ctx = new OfflineAudioContext(1, 1, info.sampleRate);
      decoded = await ctx.decodeAudioData(fileBuf.slice(0));
      $('speed-summary').hidden = false;
      const chLabel = decoded.numberOfChannels === 1 ? 'mono' : `${decoded.numberOfChannels} kanal`;
      status.textContent = `${fmtSize(f.size)} · ${comma(decoded.sampleRate / 1000, decoded.sampleRate % 1000 ? 1 : 0)} kHz · ${chLabel}` +
        (useServer() ? ' · diproses di server (FFmpeg)' : ' · diproses di browser');
    } catch (err) {
      decoded = null;
      status.textContent = '';
      showError(useServer()
        ? 'Browser tidak bisa membaca durasi file ini, tetapi file tetap bisa diproses di server.'
        : `File tidak bisa dibaca sebagai audio: ${err.message || err}`);
    } finally {
      busy = false; refresh();
    }
  }

  const input = $('speed-file'), zone = $('speed-zone');
  input.addEventListener('change', () => loadFile(input.files[0] || null));
  ['dragenter', 'dragover'].forEach(e => zone.addEventListener(e, () => zone.classList.add('dragover')));
  ['dragleave', 'drop'].forEach(e => zone.addEventListener(e, () => zone.classList.remove('dragover')));

  function showResult(blob, name, detail) {
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    resultUrl = URL.createObjectURL(blob);
    $('speed-preview').src = resultUrl;
    $('speed-download').href = resultUrl;
    $('speed-download').download = name;
    $('speed-detail').textContent = detail;
    $('speed-result').hidden = false;
  }

  run.addEventListener('click', async () => {
    if (!file || busy) return;
    showError('');
    const s = getSpeed();
    const base = file.name.replace(/\.[^.]+$/, '');
    busy = true; refresh();
    try {
      if (useServer()) {
        status.textContent = 'Mengunggah dan memproses di server…';
        const form = new FormData();
        form.append('files', file);
        form.append('speed', String(s));
        const resp = await fetch('/media/audio-speed', {method: 'POST', body: form});
        if (!resp.ok) {
          let msg = 'Pemrosesan gagal.';
          try { msg = (await resp.json()).error || msg; } catch (_) { /* not JSON */ }
          throw new Error(msg);
        }
        const blob = await resp.blob();
        const match = (resp.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";\n]+)"?/);
        const name = match ? decodeURIComponent(match[1]) : `${base}_${speedLabel(s)}x.${file.name.split('.').pop()}`;
        let dur = NaN;
        try { dur = (await new OfflineAudioContext(1, 1, decoded ? decoded.sampleRate : 48000).decodeAudioData(await blob.arrayBuffer())).duration; } catch (_) { /* leave unknown */ }
        showResult(blob, name, `Durasi hasil ${fmtTime(dur)} · ${fmtSize(blob.size)} · FFmpeg atempo`);
      } else {
        const t0 = performance.now();
        const chans = Array.from({length: decoded.numberOfChannels}, (_, c) => decoded.getChannelData(c));
        const out = await wsola(chans, decoded.sampleRate, s, p => { status.textContent = `Memproses di browser… ${Math.round(p * 100)}%`; });
        const blob = new Blob([encodeWav(out, decoded.sampleRate)], {type: 'audio/wav'});
        showResult(blob, `${base}_${speedLabel(s)}x.wav`,
          `Durasi hasil ${fmtTime(out[0].length / decoded.sampleRate)} · ${fmtSize(blob.size)} · WAV 16-bit ${comma(decoded.sampleRate / 1000, decoded.sampleRate % 1000 ? 1 : 0)} kHz`);
        status.textContent = `Selesai dalam ${comma((performance.now() - t0) / 1000, 1)} detik.`;
        return;
      }
      status.textContent = 'Selesai.';
    } catch (err) {
      status.textContent = '';
      showError(err.message || String(err));
    } finally {
      busy = false; refresh();
    }
  });

  window.addEventListener('beforeunload', () => { if (resultUrl) URL.revokeObjectURL(resultUrl); });
  setSpeed(parseFloat(range.value));
})();
