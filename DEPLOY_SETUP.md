# Deploy ACCA TOOLS ke VPS — Panduan Lengkap

App ini **Python/Flask** (bukan Next.js seperti acca-app / acca-cbt), jadi di VPS dijalankan
dengan **gunicorn** lewat **aaPanel → Python Project Manager**. Pola DNS, reverse proxy,
dan GitHub Actions sama persis dengan acca-cbt.

| Item | Nilai |
|---|---|
| Subdomain | `acca-tools.icgowa.sch.id` |
| Folder di VPS | `/www/wwwroot/acca-tools` |
| Port lokal (gunicorn) | `3003` (diatur di `gunicorn.conf.py`) |
| Proses | gunicorn `app:app`, dikelola aaPanel |
| VPS | `103.247.9.185` (Ubuntu + aaPanel), SSH `root` port `22` |

> Catatan: `acca.icgowa.sch.id` **sudah dipakai acca-app** — jangan dipakai ulang.

---

## Step 0 — Push project ke GitHub

Buat repo baru (mis. `github.com/imbank8993/acca-tools`), lalu dari komputer:

```bash
cd your-everyday-tools
git init
git add .
git commit -m "ACCA TOOLS — initial"
git branch -M main
git remote add origin git@github.com:imbank8993/acca-tools.git
git push -u origin main
```

> Branch default di sini **`main`**. Kalau Anda pakai `master`, ubah `branches: ["main"]`
> di `.github/workflows/deploy.yml` jadi `master`.

---

## Step 1 — DNS di Cloudflare

Cloudflare → domain `icgowa.sch.id` → **DNS** → **Add record**:

| Type | Name | Content | Proxy |
|---|---|---|---|
| `A` | `acca-tools` | `103.247.9.185` | Proxied (oranye) |

Hasilnya: `acca-tools.icgowa.sch.id` → VPS Anda. (Sama seperti waktu menambah `acca-cbt`.)

---

## Step 2 — Siapkan dependency sistem di VPS (sekali saja)

Beberapa tool butuh binary eksternal. Tool tetap jalan tanpa ini, tapi fitur terkait
akan nonaktif. Install yang Anda butuhkan:

```bash
# konversi dokumen (Word/PPT/Excel ↔ PDF, fidelitas tinggi)
apt update && apt install -y libreoffice

# audio/video
apt install -y ffmpeg

# OCR (PDF/gambar → teks)
apt install -y tesseract-ocr tesseract-ocr-ind   # +bahasa Indonesia

# library untuk Pillow/WeasyPrint dll (umumnya sudah ada)
apt install -y python3-venv python3-dev build-essential
```

> `python3 -m venv` wajib ada — dipakai workflow untuk membuat `.venv`.

---

## Step 3 — Clone project ke VPS

```bash
cd /www/wwwroot
git clone git@github.com:imbank8993/acca-tools.git acca-tools
cd acca-tools

# buat venv + install (workflow juga melakukan ini, tapi lakukan sekali manual dulu)
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements-core.txt -r requirements-prod.txt
./.venv/bin/pip install -r requirements-optional.txt || true
```

Tes cepat gunicorn jalan:

```bash
./.venv/bin/gunicorn -c gunicorn.conf.py app:app
# Ctrl+C untuk stop setelah lihat "Listening at: http://127.0.0.1:3003"
```

---

## Step 4 — Daftarkan proses di aaPanel (Python Project Manager)

1. Login aaPanel (`http://103.247.9.185:7800` atau sesuai setting Anda).
2. **App Store** → cari **"Python Project Manager"** → install kalau belum ada.
3. **Python Project Manager** → **Add Project**:
   - **Project path:** `/www/wwwroot/acca-tools`
   - **Run mode / framework:** pilih **gunicorn** (atau "Custom command")
   - **Startup file / command:**
     `gunicorn -c gunicorn.conf.py app:app`
   - **Port:** `3003`
   - **Python version:** gunakan venv di project (`.venv`) jika ada opsinya, atau Python 3.10+
   - **Auto start:** ON (nyala lagi setelah reboot)
4. **Start** — status harus **Running** (hijau).

> Mulai sekarang, **aaPanel** yang start/stop/restart app. Setelah tiap deploy,
> klik **Restart** di sini (atau aktifkan watch/auto-reload kalau tersedia).

---

## Step 5 — Reverse proxy + HTTPS di aaPanel

1. aaPanel → **Website** → **Add site**:
   - **Domain:** `acca-tools.icgowa.sch.id`
   - tipe situs: kosong/static dulu (kita pakai reverse proxy).
2. Buka site itu → **Reverse Proxy** → **Add**:
   - **Target URL:** `http://127.0.0.1:3003`
   - **Send Domain:** `$host`
3. Di konfigurasi proxy, pastikan ada batas upload besar (app mengizinkan 100 MB):

   ```nginx
   client_max_body_size 100m;
   proxy_read_timeout 300s;
   proxy_send_timeout 300s;
   ```

4. **SSL** → **Let's Encrypt** (atau mode Cloudflare) → aktifkan **Force HTTPS**.

Buka `https://acca-tools.icgowa.sch.id` — harus muncul ACCA TOOLS. ✅

---

## Step 6 — GitHub Secrets (untuk auto-deploy)

GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
(sama persis seperti acca-cbt):

| Secret | Value |
|---|---|
| `SSH_HOST` | `103.247.9.185` |
| `SSH_USER` | `root` |
| `SSH_KEY` | isi private key `/root/.ssh/id_ed25519` di VPS |
| `SSH_PORT` | `22` |

Deploy key GitHub untuk `git pull` di VPS: ikuti langkah yang sama seperti acca-cbt
(GitHub repo → **Settings → Deploy Keys** → paste `/root/.ssh/deploy_key.pub`, centang
Allow write access), lalu set remote:

```bash
cd /www/wwwroot/acca-tools
git remote set-url origin git@github.com:imbank8993/acca-tools.git
git pull origin main   # harus berhasil
```

---

## Alur kerja sehari-hari

1. Edit di lokal → `git push origin main`
2. GitHub Actions ("Deploy to VPS") SSH ke VPS → `git pull` → refresh `.venv`
3. Klik **Restart** di aaPanel Python Project Manager (atau watch-mode otomatis)

---

## Troubleshooting

- **502 Bad Gateway** → proses gunicorn mati. Cek di aaPanel Python Manager (Running?),
  cek log proses. Pastikan port `3003` sama antara `gunicorn.conf.py` dan reverse proxy.
- **413 Request Entity Too Large** → tambahkan `client_max_body_size 100m;` di reverse proxy.
- **Upload besar / video timeout** → naikkan `proxy_read_timeout` (Nginx) dan `timeout` (gunicorn).
- **Fitur konversi "unavailable"** → binary belum terinstall (Step 2). Cek `/capabilities`.
- **`python3 -m venv` gagal** → `apt install -y python3-venv`.
- **`Permission denied (publickey)` saat deploy** → deploy key belum terdaftar di GitHub.
