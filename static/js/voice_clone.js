(() => {
  const form = document.getElementById('voice-clone-form');
  const input = document.getElementById('voice-text');
  const button = document.getElementById('voice-submit');
  const status = document.getElementById('voice-status');
  const result = document.getElementById('voice-result');
  const error = document.getElementById('voice-error');
  const reference = document.getElementById('voice-reference');
  const referencePreview = document.getElementById('voice-reference-preview');
  let audioUrl;
  let referenceUrl;
  reference.addEventListener('change', () => {
    if (referenceUrl) URL.revokeObjectURL(referenceUrl);
    referenceUrl = reference.files[0] ? URL.createObjectURL(reference.files[0]) : null;
    referencePreview.src = referenceUrl || '';
    referencePreview.hidden = !referenceUrl;
  });
  input.addEventListener('input', () => { document.getElementById('voice-count').textContent = input.value.length; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    error.hidden = true; result.hidden = true;
    const file = reference.files[0];
    if (!file || file.size > 10 * 1024 * 1024) { error.textContent = 'Pilih WAV maksimal 10 MB.'; error.hidden = false; return; }
    button.disabled = true; status.textContent = 'Membuat audio di VPS…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Server mengembalikan HTTP ${response.status}.`);
      }
      const blob = await response.blob();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      audioUrl = URL.createObjectURL(blob);
      document.getElementById('voice-preview').src = audioUrl;
      document.getElementById('voice-download').href = audioUrl;
      result.hidden = false; status.textContent = 'Audio selesai dibuat.';
    } catch (cause) { error.textContent = cause.message; error.hidden = false; status.textContent = ''; }
    finally { button.disabled = false; }
  });
  window.addEventListener('beforeunload', () => { if (audioUrl) URL.revokeObjectURL(audioUrl); if (referenceUrl) URL.revokeObjectURL(referenceUrl); });
})();
