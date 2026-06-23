# Deploy ACCA TOOLS ke VPS — aaPanel + Webhook

App ini **Python/Flask**, dijalankan dengan **gunicorn** dan dikelola lewat **aaPanel**.
Auto-deploy memakai **aaPanel Webhook + Deploy Key** (TANPA GitHub Actions, TANPA 4 secret) —
sama dengan cara Anda mengelola acca-cbt di aaPanel.

| Item | Nilai |
|---|---|
| Subdomain | `acca-tools.icgowa.sch.id` |
| Folder di VPS | `/www/wwwroot/acca-tools` |
| Repo | `git@github.com:imbank8993/acca-tools.git` (privat) |
| Port lokal (gunicorn) | `3004` (`gunicorn.conf.py`) |
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
| `A` | `acca-tools` | `194.233.81.192` | Proxied (oranye) |

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
# lihat "Listening at: http://127.0.0.1:3004", lalu Ctrl+C
```

---

## Step 5 — Jalankan proses dengan systemd (cara terpakai)

Port `3003` sudah dipakai app Next.js lain di VPS — itu sebabnya app ini pakai `3004`.
Proses dijalankan permanen lewat **systemd** (auto-start saat boot, auto-restart kalau crash):

```bash
sudo cp deploy/acca-tools.service /etc/systemd/system/acca-tools.service
sudo systemctl daemon-reload
sudo systemctl enable --now acca-tools

# verifikasi
systemctl status acca-tools --no-pager
ss -ltnp | grep 3004
curl -I http://127.0.0.1:3004     # harus HTTP/1.1 200 OK
```

Perintah harian:
```bash
systemctl reload acca-tools     # reload worker tanpa downtime (HUP)
systemctl restart acca-tools    # restart penuh
journalctl -u acca-tools -n 50  # lihat log
```

> **Alternatif aaPanel Python Project Manager:** kalau ingin semua dalam panel, bisa juga —
> startup command `/www/wwwroot/acca-tools/.venv/bin/gunicorn -c gunicorn.conf.py app:app`,
> port `3004`, Auto-start ON. **Pilih salah satu saja** (systemd ATAU aaPanel), jangan dua-duanya
> agar tidak rebutan port 3004.

---

## Step 6 — Reverse proxy + HTTPS di aaPanel

1. aaPanel → **Website** → **Add site** → domain `acca-tools.icgowa.sch.id`.
2. Buka site → **Reverse Proxy** → **Add** → Target `http://127.0.0.1:3004`, Send Domain `$host`.
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
   - aaPanel akan memberi **URL webhook** (mis. `http://194.233.81.192:port/hook?name=acca-tools-deploy&secret=...`).
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
  pastikan port `3004` sama di `gunicorn.conf.py` dan reverse proxy.
- **413 Request Entity Too Large** → tambahkan `client_max_body_size 100m;` di reverse proxy.
- **Upload/konversi besar timeout** → naikkan `proxy_read_timeout` (Nginx) & `timeout` (gunicorn).
- **Webhook tidak jalan** → cek "Recent Deliveries" di GitHub Webhooks; pastikan port webhook aaPanel
  terbuka di firewall; pastikan `deploy.sh` executable (`chmod +x`).
- **`git pull` minta password / Permission denied** → Deploy key belum terdaftar, atau remote belum
  pakai `git@github-acca-tools:...` (Host alias di `~/.ssh/config`).
- **`gunicorn.pid not found`** → app belum di-start lewat aaPanel, atau `pidfile` di `gunicorn.conf.py`
  tidak ter-load. Restart sekali dari aaPanel.
- **Fitur konversi "unavailable"** → binary opsional belum terpasang (Step 2).

---

## Step 8 — SSO: hanya bisa diakses setelah login acca-app

ACCA TOOLS bisa dikunci di belakang login acca-app. Alurnya: user buka tools tanpa
sesi → ditendang ke `acca-app/api/sso/tools` → acca-app menandatangani JWT pendek →
balik ke `tools/auth/sso?token=...` → Flask buka sesi. **Wajib HTTPS.**

SSO **aktif hanya jika `TOOLS_SSO_SECRET` di-set**. Tanpa itu, app terbuka (cocok untuk dev).

### Di acca-tools (VPS)

Buat file `/www/wwwroot/acca-tools/.env` (otomatis dibaca systemd, tidak ikut git):

```ini
TOOLS_SSO_SECRET=<secret-acak-yang-sama-dengan-acca-app>
FLASK_SECRET_KEY=<secret-acak-lain-khusus-tools>
ACCA_APP_URL=https://acca.icgowa.sch.id
# SESSION_COOKIE_SECURE=0   # hanya kalau tes via http (tanpa SSL)
```

Buat secret acak:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Lalu:
```bash
sudo systemctl restart acca-tools
```

### Di acca-app (repo Next.js terpisah)

1. File baru `app/api/sso/tools/route.ts` (sudah disiapkan) — menandatangani token.
2. Env (`.env.production` / Vercel):
   - `TOOLS_SSO_SECRET=` **(sama persis dengan acca-tools)**
   - `NEXT_PUBLIC_TOOLS_URL=https://unelma.site` (URL tools saat ini)
3. (Opsional) Tombol "Buka ACCA TOOLS" yang menaut ke `/api/sso/tools`.
4. Deploy acca-app.

> `FLASK_SECRET_KEY` cukup di acca-tools (untuk tanda tangan cookie sesi).
> `TOOLS_SSO_SECRET` harus **identik** di kedua app.
