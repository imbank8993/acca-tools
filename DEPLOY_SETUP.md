# Deploy ACCA TOOLS ke VPS — aaPanel + Webhook

App ini **Python/Flask**, dijalankan dengan **gunicorn** dan dikelola lewat **aaPanel**.
Auto-deploy memakai **aaPanel Webhook + Deploy Key** (TANPA GitHub Actions, TANPA 4 secret) —
sama dengan cara Anda mengelola acca-cbt di aaPanel.

| Item | Nilai |
|---|---|
| Subdomain | `acca-tools.icgowa.sch.id` |
| Folder di VPS | `/www/wwwroot/acca-tools` |
| Repo | `git@github.com:imbank8993/acca-tools.git` (privat) |
| Port lokal (gunicorn) | `3003` (`gunicorn.conf.py`) |
| Auto-deploy | GitHub Webhook → aaPanel jalankan `scripts/deploy.sh` |

> `acca.icgowa.sch.id` sudah dipakai acca-app — jangan dipakai ulang.

---

## Bagaimana auto-deploy bekerja

```
git push (main) ─► GitHub kirim WEBHOOK ─► aaPanel di VPS ─► scripts/deploy.sh
                                                              ├─ git pull       (pakai DEPLOY KEY read-only)
                                                              ├─ pip install
                                                              └─ kill -HUP gunicorn  (reload tanpa downtime)
```

- **Deploy Key (read-only)** = izin agar VPS bisa `git pull` repo privat. Bukan pemicu.
- **Webhook** = pemicu otomatis saat ada push.
- **4 GitHub Secrets TIDAK diperlukan** (itu hanya untuk GitHub Actions).

---

## Step 1 — DNS di Cloudflare

Cloudflare → `icgowa.sch.id` → **DNS** → **Add record**:

| Type | Name | Content | Proxy |
|---|---|---|---|
| `A` | `acca-tools` | `103.247.9.185` | Proxied (oranye) |

---

## Step 2 — Dependency sistem di VPS (sekali saja)

```bash
apt update
apt install -y python3-venv python3-dev build-essential   # wajib
apt install -y libreoffice                                # konversi Office ↔ PDF (opsional)
apt install -y ffmpeg                                      # audio/video (opsional)
apt install -y tesseract-ocr tesseract-ocr-ind            # OCR + bahasa Indonesia (opsional)
```

Tool tetap jalan tanpa paket opsional — fitur terkait saja yang nonaktif (cek di `/capabilities`).

---

## Step 3 — Deploy Key (read-only) agar VPS bisa `git pull`

```bash
# Di VPS — buat key khusus repo ini (jangan pakai ulang key repo lain)
ssh-keygen -t ed25519 -f /root/.ssh/acca-tools_deploy -N "" -q
cat /root/.ssh/acca-tools_deploy.pub   # salin output-nya
```

GitHub → repo **acca-tools** → **Settings → Deploy keys → Add deploy key** →
tempel public key → **JANGAN** centang "Allow write access" (cukup read-only) → **Add key**.

Karena VPS Anda punya beberapa repo dari `github.com`, pakai **Host alias** agar key-nya tidak bentrok:

```bash
# Di VPS — tambahkan ke ~/.ssh/config
cat >> /root/.ssh/config <<'EOF'

Host github-acca-tools
  HostName github.com
  IdentityFile ~/.ssh/acca-tools_deploy
  IdentitiesOnly yes
EOF
```

---

## Step 4 — Clone & siapkan venv

```bash
cd /www/wwwroot
git clone git@github-acca-tools:imbank8993/acca-tools.git acca-tools
cd acca-tools

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements-core.txt -r requirements-prod.txt
./.venv/bin/pip install -r requirements-optional.txt || true

# pastikan origin memakai host alias (agar deploy.sh bisa pull tanpa diminta password)
git remote set-url origin git@github-acca-tools:imbank8993/acca-tools.git
git pull origin main    # tes — harus berhasil tanpa prompt
```

Tes gunicorn jalan:

```bash
./.venv/bin/gunicorn -c gunicorn.conf.py app:app
# lihat "Listening at: http://127.0.0.1:3003", lalu Ctrl+C
```

---

## Step 5 — Jalankan proses di aaPanel (Python Project Manager)

1. aaPanel → **App Store** → install **Python Project Manager** (jika belum ada).
2. **Add Project**:
   - **Project path:** `/www/wwwroot/acca-tools`
   - **Startup command:** `gunicorn -c gunicorn.conf.py app:app`
   - **Port:** `3003`
   - **Python:** gunakan `.venv` project (atau Python 3.10+)
   - **Auto start:** ON
3. **Start** — status harus **Running** (hijau). File `gunicorn.pid` akan muncul di folder project.

---

## Step 6 — Reverse proxy + HTTPS di aaPanel

1. aaPanel → **Website** → **Add site** → domain `acca-tools.icgowa.sch.id`.
2. Buka site → **Reverse Proxy** → **Add** → Target `http://127.0.0.1:3003`, Send Domain `$host`.
3. Tambahkan di konfigurasi proxy (app mengizinkan upload 100 MB):

   ```nginx
   client_max_body_size 100m;
   proxy_read_timeout 300s;
   proxy_send_timeout 300s;
   ```

4. **SSL** → Let's Encrypt (atau mode Cloudflare) → aktifkan **Force HTTPS**.

Buka `https://acca-tools.icgowa.sch.id` — harus muncul ACCA TOOLS. ✅

---

## Step 7 — Auto-deploy via Webhook

1. Pastikan skrip bisa dieksekusi: `chmod +x /www/wwwroot/acca-tools/scripts/deploy.sh`
2. aaPanel → **App Store** → install **Webhook** (jika belum ada).
3. Buka **Webhook** → **Add Hook**:
   - **Name:** `acca-tools-deploy`
   - **Script / Execute:** `bash /www/wwwroot/acca-tools/scripts/deploy.sh`
   - aaPanel akan memberi **URL webhook** (mis. `http://103.247.9.185:port/hook?name=acca-tools-deploy&secret=...`).
4. GitHub → repo **acca-tools** → **Settings → Webhooks → Add webhook**:
   - **Payload URL:** tempel URL dari aaPanel
   - **Content type:** `application/json`
   - **Events:** *Just the push event*
   - **Active:** ✓
5. Tes: ubah sesuatu → `git push` → cek di GitHub tab **Webhooks** (centang hijau "Recent Deliveries"),
   dan `scripts/deploy.sh` akan menjalankan pull + reload gunicorn otomatis.

> `scripts/deploy.sh` melakukan `git pull` → `pip install` → `kill -HUP` (reload gunicorn
> tanpa downtime, memanfaatkan `gunicorn.pid`). Tidak perlu klik restart manual.

---

## Alur kerja sehari-hari

1. Edit di lokal → `git push origin main`
2. GitHub panggil webhook → aaPanel jalankan `scripts/deploy.sh`
3. App ter-update otomatis (gunicorn reload). Selesai.

---

## Troubleshooting

- **502 Bad Gateway** → proses gunicorn mati / port salah. Cek aaPanel Python Manager (Running?),
  pastikan port `3003` sama di `gunicorn.conf.py` dan reverse proxy.
- **413 Request Entity Too Large** → tambahkan `client_max_body_size 100m;` di reverse proxy.
- **Upload/konversi besar timeout** → naikkan `proxy_read_timeout` (Nginx) & `timeout` (gunicorn).
- **Webhook tidak jalan** → cek "Recent Deliveries" di GitHub Webhooks; pastikan port webhook aaPanel
  terbuka di firewall; pastikan `deploy.sh` executable (`chmod +x`).
- **`git pull` minta password / Permission denied** → Deploy key belum terdaftar, atau remote belum
  pakai `git@github-acca-tools:...` (Host alias di `~/.ssh/config`).
- **`gunicorn.pid not found`** → app belum di-start lewat aaPanel, atau `pidfile` di `gunicorn.conf.py`
  tidak ter-load. Restart sekali dari aaPanel.
- **Fitur konversi "unavailable"** → binary opsional belum terpasang (Step 2).
