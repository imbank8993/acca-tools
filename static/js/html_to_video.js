(() => {
  const form = document.getElementById('html-video-form');
  const button = document.getElementById('video-submit');
  const status = document.getElementById('video-status');
  const result = document.getElementById('video-result');
  const error = document.getElementById('video-error');
  const input = document.getElementById('html-file');
  const durationMode = document.getElementById('video-duration-mode');
  const fpsMode = document.getElementById('video-fps');
  durationMode.addEventListener('change', () => {
    document.getElementById('video-custom-duration').hidden = durationMode.value !== 'manual';
  });
  let objectUrl;
  input.addEventListener('change', async () => {
    const file = input.files[0];
    document.getElementById('video-file-label').textContent = file?.name || 'Klik untuk memilih .html atau .htm';
    const detected = document.getElementById('video-detected');
    if (!file || file.size > 2 * 1024 * 1024) { detected.textContent = ''; return; }
    const html = await file.text();
    const seconds = html.match(/\b(?:const|let|var)\s+(?:DUR|DURATION|TOTAL_DURATION|VIDEO_DURATION)\s*=\s*(\d+(?:\.\d+)?)/i)?.[1];
    const fps = html.match(/\.captureStream\(\s*(\d+(?:\.\d+)?)\s*\)/i)?.[1];
    const canvas = html.match(/<canvas\b[^>]*>/i)?.[0];
    const width = canvas?.match(/\bwidth=["']?(\d+)/i)?.[1];
    const height = canvas?.match(/\bheight=["']?(\d+)/i)?.[1];
    const parts = [seconds && `${seconds} detik`, fps && `${fps} FPS`, width && height && `${width} × ${height} piksel`].filter(Boolean);
    detected.textContent = parts.length ? `Terdeteksi dari HTML: ${parts.join(' · ')}` : 'Timeline dan FPS akan diperiksa saat proses render.';
    durationMode.options[0].textContent = seconds ? `Ikuti HTML · ${seconds} detik` : 'Ikuti timeline HTML';
    fpsMode.options[0].textContent = fps ? `Ikuti HTML · ${fps} FPS` : 'Ikuti FPS HTML';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    error.hidden = true; result.hidden = true;
    const file = input.files[0];
    if (!file || file.size > 2 * 1024 * 1024) { error.textContent = 'Pilih HTML berukuran maksimal 2 MB.'; error.hidden = false; return; }
    button.disabled = true; status.textContent = 'Merender frame dan membuat MP4…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Server mengembalikan HTTP ${response.status}.`);
      }
      const blob = await response.blob();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      document.getElementById('video-preview').src = objectUrl;
      document.getElementById('video-download').href = objectUrl;
      result.hidden = false;
      const width = response.headers.get('X-Video-Width');
      const height = response.headers.get('X-Video-Height');
      const duration = response.headers.get('X-Video-Duration');
      const fps = response.headers.get('X-Video-Fps');
      status.textContent = width && height && duration && fps
        ? `Selesai · ${duration} detik · ${fps} FPS · ${width} × ${height} piksel.`
        : 'Video selesai dibuat.';
    } catch (cause) { error.textContent = cause.message; error.hidden = false; status.textContent = ''; }
    finally { button.disabled = false; }
  });
  window.addEventListener('beforeunload', () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
})();
