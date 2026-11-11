(() => {
  const form = document.getElementById('html-video-form');
  const button = document.getElementById('video-submit');
  const status = document.getElementById('video-status');
  const result = document.getElementById('video-result');
  const error = document.getElementById('video-error');
  let objectUrl;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    error.hidden = true; result.hidden = true;
    const file = document.getElementById('html-file').files[0];
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
      result.hidden = false; status.textContent = 'Video selesai dibuat.';
    } catch (cause) { error.textContent = cause.message; error.hidden = false; status.textContent = ''; }
    finally { button.disabled = false; }
  });
  window.addEventListener('beforeunload', () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
})();
