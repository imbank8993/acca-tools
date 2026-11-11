(() => {
  const $ = id => document.getElementById(id);
  const start = $('record-start'), pause = $('record-pause'), stop = $('record-stop');
  const status = $('record-status'), clock = $('record-time'), error = $('record-error');
  const meter = document.querySelector('.level-track'), fill = $('level-fill');
  let stream, context, source, worklet, analyser, mute, chunks = [], samples = 0;
  let timer, tickStart = 0, elapsed = 0, paused = false, playingUrl;
  const maxSeconds = 600;

  function showError(message) { error.textContent = message; error.hidden = false; }
  function format(seconds) { return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`; }
  function updateClock() {
    const seconds = elapsed + (paused ? 0 : (performance.now() - tickStart) / 1000);
    clock.textContent = format(seconds);
    if (seconds >= maxSeconds) finish();
  }
  async function devices() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const current = $('mic-device').value;
    try {
      const inputs = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput');
      $('mic-device').replaceChildren(new Option('Default microphone', ''));
      inputs.forEach((device, i) => $('mic-device').add(new Option(device.label || `Microphone ${i + 1}`, device.deviceId)));
      $('mic-device').value = [...$('mic-device').options].some(item => item.value === current) ? current : '';
    } catch (_) { /* Browser may withhold device names before permission. */ }
  }
  function drawLevel() {
    if (!analyser) return;
    const values = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(values);
    let sum = 0;
    for (const value of values) sum += ((value - 128) / 128) ** 2;
    const level = Math.min(100, Math.round(Math.sqrt(sum / values.length) * 240));
    fill.style.width = `${level}%`;
    meter.setAttribute('aria-valuenow', String(level));
    if (stream) requestAnimationFrame(drawLevel);
  }
  function wavBlob(data, total, rate) {
    const buffer = new ArrayBuffer(44 + total * 2), view = new DataView(buffer);
    const word = (offset, value) => view.setUint32(offset, value, true);
    const short = (offset, value) => view.setUint16(offset, value, true);
    ['RIFF', 'WAVE', 'fmt ', 'data'].forEach((tag, index) => {
      const offset = [0, 8, 12, 36][index];
      for (let i = 0; i < 4; i++) view.setUint8(offset + i, tag.charCodeAt(i));
    });
    word(4, 36 + total * 2); word(16, 16); short(20, 1); short(22, 1);
    word(24, rate); word(28, rate * 2); short(32, 2); short(34, 16); word(40, total * 2);
    let offset = 44;
    for (const chunk of data) for (const sample of chunk) {
      const limited = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, limited < 0 ? limited * 32768 : limited * 32767, true);
      offset += 2;
    }
    return new Blob([buffer], {type: 'audio/wav'});
  }
  async function cleanup() {
    clearInterval(timer);
    if (worklet) { worklet.port.onmessage = null; worklet.disconnect(); }
    if (source) source.disconnect();
    if (analyser) analyser.disconnect();
    if (mute) mute.disconnect();
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null; worklet = null; analyser = null; source = null;
    if (context) await context.close();
    context = null; fill.style.width = '0%'; meter.setAttribute('aria-valuenow', '0');
  }
  async function finish() {
    if (!stream) return;
    if (!paused) elapsed += (performance.now() - tickStart) / 1000;
    paused = true;
    const rate = context.sampleRate;
    await cleanup();
    start.disabled = false; pause.disabled = true; pause.textContent = 'Pause'; stop.disabled = true;
    $('record-indicator').classList.remove('active');
    clock.textContent = format(elapsed);
    if (!samples) { status.textContent = 'No audio recorded'; showError('The recording is empty. Please try again.'); return; }
    if (playingUrl) URL.revokeObjectURL(playingUrl);
    const blob = wavBlob(chunks, samples, rate);
    playingUrl = URL.createObjectURL(blob);
    $('record-preview').src = playingUrl;
    $('record-download').href = playingUrl;
    $('record-download').download = `acca-recording-${new Date().toISOString().replace(/[:.]/g, '-')}.wav`;
    $('record-detail').textContent = `${(blob.size / 1024 / 1024).toFixed(1)} MB · ${Math.round(rate / 1000)} kHz · PCM 16-bit`;
    $('record-result').hidden = false;
    status.textContent = 'Recording complete';
    chunks = []; samples = 0;
  }
  start.onclick = async () => {
    error.hidden = true;
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) { showError('This browser does not support WAV recording. Use an up-to-date browser over HTTPS or localhost.'); return; }
    start.disabled = true;
    try {
      const speech = $('audio-profile').value === 'speech';
      stream = await navigator.mediaDevices.getUserMedia({audio: {
        deviceId: $('mic-device').value ? {exact: $('mic-device').value} : undefined,
        channelCount: 1, echoCancellation: speech, noiseSuppression: speech, autoGainControl: speech,
      }});
      await devices();
      context = new AudioContext({sampleRate: 48000});
      await context.audioWorklet.addModule('/static/js/pcm_recorder_processor.js');
      source = context.createMediaStreamSource(stream);
      analyser = context.createAnalyser(); analyser.fftSize = 2048;
      worklet = new AudioWorkletNode(context, 'pcm-recorder');
      mute = context.createGain(); mute.gain.value = 0;
      source.connect(analyser); source.connect(worklet); worklet.connect(mute); mute.connect(context.destination);
      chunks = []; samples = 0; elapsed = 0; paused = false; tickStart = performance.now();
      worklet.port.onmessage = event => { chunks.push(event.data); samples += event.data.length; };
      $('record-result').hidden = true;
      $('record-indicator').classList.add('active');
      status.textContent = 'Recording'; pause.disabled = false; stop.disabled = false;
      timer = setInterval(updateClock, 250); drawLevel();
    } catch (cause) {
      await cleanup(); start.disabled = false;
      showError(cause.name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in your browser settings.' : `Could not open the microphone: ${cause.message}`);
    }
  };
  pause.onclick = () => {
    if (!worklet) return;
    if (!paused) { elapsed += (performance.now() - tickStart) / 1000; paused = true; worklet.port.postMessage('pause'); pause.textContent = 'Resume'; status.textContent = 'Paused'; }
    else { tickStart = performance.now(); paused = false; worklet.port.postMessage('resume'); pause.textContent = 'Pause'; status.textContent = 'Recording'; }
    updateClock();
  };
  stop.onclick = finish;
  window.addEventListener('beforeunload', () => { if (playingUrl) URL.revokeObjectURL(playingUrl); if (stream) stream.getTracks().forEach(track => track.stop()); });
  devices();
})();
