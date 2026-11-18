# Panduan Setup Rack HP → ADB → Appium → Dashboard

Dokumen ini menjelaskan langkah operasional dari **memasang HP di rack**,
menyiapkan **USB debugging + ADB**, menjalankan **satu Appium server per HP**,
membuat **manifest rack**, sampai HP terhubung dan terintegrasi dengan
`SocialMediaBot` + dashboard web.

Target contoh: **20 HP Android** dengan Appium port `4723..4742`.

> **Angka 20 di dokumen ini hanya contoh.** Jika rack Anda berisi 8, 10, 12,
> atau jumlah lain, ganti semua angka 20/`4742` dengan jumlah dan port aktual.
> `generate_rack.py` membuat port otomatis sesuai jumlah HP yang terdeteksi.

> Perintah diberikan untuk **Windows PowerShell** dan **macOS/Linux**. Di
> Windows gunakan `python` atau `py -3`; di macOS/Linux gunakan `python3`.
> Semua perintah `python` di bawah boleh diganti `py -3` di Windows.

## 1. Pahami arsitektur dulu

Aplikasi ini memakai aturan: **1 HP = 1 Appium server = 1 port**.

### Opsi A — semua di satu PC (paling mudah)

```text
HP slot 01..20
   │ USB
   ▼
Powered USB hub
   │ USB
   ▼
PC rack
├── adb server (mendeteksi semua HP)
├── Appium :4723  ← session HP slot 01
├── Appium :4724  ← session HP slot 02
├── ...
├── Appium :4742  ← session HP slot 20
└── dashboard.py :8080
```

Manifest memakai `"host": "127.0.0.1"` (atau field `host` dihilangkan).

### Opsi B — dashboard diakses dari PC lain lewat WiFi/LAN

**B1 — disarankan: dashboard tetap berjalan di PC server.**

```text
HP 01..20 ──USB──> PC server
                    ├── adb
                    ├── Appium :4723..4742
                    └── dashboard.py :8080  ← satu-satunya port yang dibuka
                              │ LAN/WiFi
                              ▼
                    PC lain: browser http://IP-PC-SERVER:8080
```

PC lain **hanya membuka browser**; Appium dan ADB tetap lokal di PC server.
Manifest/Setup rack tetap memakai Appium host `127.0.0.1`. Hanya port dashboard
yang perlu diizinkan di firewall.

**B2 — proses dashboard dipindah ke PC lain (lebih kompleks).**

```text
HP 01..20 ──USB──> PC rack (adb + Appium :4723..4742)
                            │ LAN
                            ▼
                    PC controller (dashboard.py)
```

Manifest memakai `"host": "192.168.1.20"` atau IP PC rack, dan **semua port
Appium** harus bisa diakses dari PC controller; buka firewall LAN secukupnya.
Model ini kurang aman karena Appium tidak punya autentikasi.

> ADB dan Appium harus berada di mesin yang sama dengan kabel USB HP. Dashboard
> boleh di mesin lain karena hanya mengakses HTTP API Appium.

## 2. Checklist cepat

1. Pasang HP di rack, beri label **slot-01 … slot-20**.
2. Aktifkan **Developer options** + **USB debugging** di setiap HP.
3. Pasang **powered USB hub**, sambungkan HP, izinkan **Allow USB debugging**.
4. Install **Android platform-tools (adb)** dan pastikan semua HP (contoh: 20)
   berstatus `device`.
5. Catat pemetaan **slot fisik ↔ serial ADB (UDID)**.
6. Install **Node.js LTS**, Appium, dan driver `uiautomator2`.
7. Jalankan Appium untuk setiap port HP, pastikan semuanya merespons `/status`.
8. Install Python + requirements. Jalankan dashboard; jika manifest belum ada,
   dashboard masuk mode setup dan device diisi lewat UI (`Scan ADB`).
9. Di dashboard: **Setup rack → Scan ADB → Simpan & terapkan**, lalu
   **Connect all**.
10. Tes live screen dan satu aksi pada satu device.

## 3. Prasyarat

### Hardware

- HP Android dengan USB debugging; idealnya model/versi Android seragam.
- **Powered USB hub** (bukan hub pasif). Untuk 20 HP, gunakan 2–4 hub dan
  sebarkan ke beberapa controller USB (mis. PCIe USB card) agar bandwidth dan
  daya stabil.
- Kabel USB **data** berkualitas (bukan kabel charge-only), panjang secukupnya.
- PC rack dengan RAM memadai: 20 proses Appium (Node) biasanya membutuhkan
  sekitar **6–8 GB RAM** khusus Appium + adb, di luar beban OS.
- Daya listrik stabil, pendinginan cukup, dan ruang agar HP tidak overheat.
- Jaringan LAN bila dashboard dipisah dari PC rack.

### Software

| Komponen | Versi/di mana | Fungsi |
| --- | --- | --- |
| [Python](https://www.python.org/downloads/) | 3.9+ | `SocialMediaBot`, dashboard, test |
| [Node.js](https://nodejs.org/) | LTS | menjalankan Appium |
| [Appium](https://appium.io/) | 2.x/3.x | server otomasi per port |
| Driver `uiautomator2` | terbaru | driver Android |
| [Android platform-tools](https://developer.android.com/studio/releases/platform-tools) | terbaru | `adb` |
| Browser modern | Chrome/Edge/Firefox | dashboard |

Akun media sosial **sudah login manual** di setiap HP sebelum otomasi. Aplikasi
tidak mengisi password, captcha, atau 2FA untuk Anda.

## 4. Pasang HP secara fisik

1. Beri label fisik pada setiap slot: `01`, `02`, … `20`. Label ini harus sama
   dengan `device_name` di manifest (`slot-01`, `slot-02`, …).
2. Letakkan powered hub di rak; sambungkan adaptor daya hub lebih dulu.
3. Gunakan port USB hub yang stabil; hindari menumpuk 20 HP pada satu
   controller USB.
4. Sambungkan kabel data ke HP dan hub. Jangan memakai kabel charge-only.
5. Nyalakan HP, buka kunci layar, dan biarkan layar menyala selama setup.
6. Setelah semua terpasang, cek satu per satu di PC: cabut/sambung kabel
   memastikan slot yang benar terdeteksi.
7. Catat di tabel kerja: **slot fisik, serial ADB, model, versi Android**.

> **Penting:** jangan memindahkan kabel antar-port saat Appium session aktif.
> Jika kabel berpindah, Appium bisa kehilangan device dan session perlu dibuat
> ulang.

## 5. Setting Android di setiap HP

Lakukan pada semua HP. Untuk 20 HP, siapkan checklist dan centang per slot.

### 5.1 Developer options & USB debugging

1. Buka **Settings → About phone**.
2. Tap **Build number** 7× sampai muncul “You are now a developer”.
3. Buka **Settings → System → Developer options**.
4. Aktifkan **USB debugging**.
5. Aktifkan **Stay awake while charging** (atau “Stay awake”).
6. Set **USB configuration** ke **File transfer / MTP** bila ADB tidak muncul
   pada model tertentu.
7. Saat HP terhubung dan muncul dialog **Allow USB debugging?**, centang
   **Always allow from this computer**, lalu **Allow/OK**.

### 5.2 Setelan yang disarankan untuk farm

- **Screen lock:** `None` atau `Swipe` agar Appium tidak tertahan lock screen.
- **Screen timeout:** panjang (mis. 10 menit) atau gunakan “Stay awake”.
- **Auto-rotate:** matikan agar koordinat live screen stabil.
- **Adaptive battery / battery optimization:** kecualikan aplikasi yang
  diotomasi dan Appium/ADB bila ada.
- **Animasi:** matikan lewat ADB (lihat bawah) untuk mempercepat UI test.
- **Tanggal & waktu:** otomatis.
- **Notifikasi:** matikan seperlunya agar tidak menutupi tombol aplikasi.
- **Google Play Protect / auto-update:** pertimbangkan menonaktifkan update
  otomatis saat jam produksi agar UI aplikasi tidak berubah tiba-tiba.

### 5.3 Login akun media sosial

Lakukan manual di setiap HP:

- install Facebook/Instagram/Threads/X/TikTok;
- login akun yang sudah disiapkan;
- selesaikan captcha/2FA/checkpoint;
- buka aplikasi sekali agar data awal ter-cache;
- pastikan akun tidak logout saat HP restart (`no_reset=True` membantu
  mempertahankan data aplikasi, tetapi bukan pengganti login manual).

> Satu akun sebaiknya satu HP. Jangan memakai akun yang sama di banyak HP
> secara paralel.

## 6. Install ADB dan verifikasi koneksi

### 6.1 Windows

1. Unduh **Android SDK Platform-Tools** dari
   [developer.android.com](https://developer.android.com/studio/releases/platform-tools).
2. Ekstrak ke folder tanpa spasi, misalnya `C:\platform-tools`.
3. Tambahkan folder itu ke `PATH` user (buka PowerShell baru setelahnya):

```powershell
$old = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$old;C:\platform-tools", "User")
```

4. Install driver USB sesuai merek HP (Samsung/Xiaomi/Oppo/Google, dll.) bila
   Windows belum mengenali perangkat. Hindari driver “universal” tidak resmi.
5. Verifikasi:

```powershell
adb version
where.exe adb
```

### 6.2 macOS

```bash
brew install --cask android-platform-tools
adb version
```

Alternatif: install Android Studio → SDK Manager → **Android SDK Platform-Tools**.

### 6.3 Linux

```bash
sudo apt update
sudo apt install android-tools-adb android-tools-fastboot
adb version
```

Untuk Ubuntu/Debian, pastikan user masuk grup `plugdev` dan udev rules Android
sudah terpasang (biasanya dari paket Android Studio). Logout/login setelah
mengubah grup.

### 6.4 Authorization pertama

1. Hubungkan **satu HP** terlebih dahulu.
2. Jalankan:

```bash
adb start-server
adb devices -l
```

3. Di HP akan muncul **Allow USB debugging?** → centang **Always allow from
   this computer** → **Allow**.
4. Ulangi hingga semua HP terhubung.

Output sehat terlihat seperti:

```text
List of devices attached
R58M00000001    device product:... model:SM_A525F device:a52q
R58M00000002    device product:... model:SM_A525F device:a52q
```

Status yang mungkin muncul:

| Status | Arti | Tindakan |
| --- | --- | --- |
| `device` | siap dipakai | lanjut |
| `unauthorized` | belum approve RSA | buka HP, approve; ulangi `adb kill-server` lalu `adb start-server` |
| `offline` | koneksi USB bermasalah | cabut/sambung, ganti kabel/port, `adb reconnect` |
| tidak muncul | driver/kabel/mode USB | cek kabel data, driver OEM, USB configuration MTP |

### 6.5 Verifikasi semua HP

macOS/Linux:

```bash
adb devices | awk 'NR>1 && $2=="device" {count++} END {print count+0 " device siap"}'
```

Windows PowerShell:

```powershell
(adb devices | Select-String "`tdevice$").Count
```

Target: jumlah HP yang terpasang (contoh 20). Jika kurang, selesaikan satu per
satu sebelum lanjut ke Appium.

### 6.6 Catat mapping slot ↔ UDID

UDID di manifest **harus persis** serial dari `adb devices` (bukan nama model).

macOS/Linux:

```bash
adb devices | awk 'NR>1 && $2=="device" {print $1}' | while read -r serial; do
  model=$(adb -s "$serial" shell getprop ro.product.model | tr -d '\r')
  android=$(adb -s "$serial" shell getprop ro.build.version.release | tr -d '\r')
  printf '%s\t%s\t%s\n' "$serial" "$model" "$android"
done
```

Windows PowerShell:

```powershell
adb devices | Select-String "`tdevice$" | ForEach-Object {
  $serial = ($_ -split "`t")[0]
  $model = (adb -s $serial shell getprop ro.product.model).Trim()
  $android = (adb -s $serial shell getprop ro.build.version.release).Trim()
  [pscustomobject]@{ Slot = ""; Serial = $serial; Model = $model; Android = $android }
} | Format-Table -AutoSize
```

Isi kolom `Slot` manual sesuai label fisik, lalu simpan tabel ini. Contoh:

| Slot | UDID/serial | Model | Android | Appium port |
| --- | --- | --- | --- | --- |
| slot-01 | R58M00000001 | SM-A525F | 14 | 4723 |
| slot-02 | R58M00000002 | SM-A525F | 14 | 4724 |
| … | … | … | … | … |
| slot-20 | R58M00000020 | SM-A525F | 14 | 4742 |

> Jangan memakai `adb kill-server` saat Appium session aktif. Jika perlu
> restart ADB, tutup dulu semua session/dashboard.

### 6.7 Tuning Android lewat ADB (opsional, disarankan)

Jalankan per HP dengan mengganti `SERIAL` memakai UDID dari tabel mapping:

```bash
adb -s SERIAL shell settings put global stay_on_while_plugged_in 3
adb -s SERIAL shell settings put system screen_off_timeout 600000
adb -s SERIAL shell settings put global window_animation_scale 0
adb -s SERIAL shell settings put global transition_animation_scale 0
adb -s SERIAL shell settings put global animator_duration_scale 0
adb -s SERIAL shell wm size
adb -s SERIAL shell getprop ro.build.version.release
```

`stay_on_while_plugged_in=3` berarti tetap menyala saat USB + charger. Matikan
animasi agar UI test lebih cepat dan stabil. Perintah `wm size` membantu
mengetahui resolusi asli HP; dashboard juga menampilkan resolusi screenshot
live.

## 7. Install Node.js dan Appium

### 7.1 Install Node.js LTS

Windows (winget tersedia di Windows 10/11 terbaru):

```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

Atau unduh installer LTS dari [nodejs.org](https://nodejs.org/).

macOS:

```bash
brew install node
```

Linux (Ubuntu/Debian) — gunakan NodeSource LTS agar Node tidak terlalu lama:

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
```

Atau gunakan `nvm`/installer resmi. Appium 2.x/3.x sebaiknya memakai **Node.js
20 LTS atau lebih baru**.

Setelah install, **buka terminal baru**, lalu verifikasi:

```bash
node --version
npm --version
```

### 7.2 Install Appium dan driver

1. Install Appium global:

```bash
npm install -g appium
appium --version
```

Jika muncul error permission di macOS/Linux, gunakan `nvm` atau perbaiki prefix
npm; hindari `sudo npm` untuk instalasi global bila memungkinkan.

2. Install driver Android:

```bash
appium driver install uiautomator2
appium driver list --installed
```

3. Opsional, cek kesehatan driver:

```bash
appium driver doctor uiautomator2
```

4. Pastikan environment Android terbaca oleh Appium:
   - Windows: `ANDROID_HOME` / `ANDROID_SDK_ROOT` mengarah ke Android SDK,
     `platform-tools` ada di `PATH`.
   - macOS/Linux: SDK dari Android Studio atau platform-tools sudah di `PATH`.

> Appium server perlu melihat `adb`. Jika `adb devices` berjalan di terminal
> tetapi Appium gagal, restart terminal/Appium setelah memperbaiki `PATH`.

## 8. Jalankan satu Appium server per HP

### 8.1 Tes satu server dulu

```bash
appium -p 4723
```

Buka terminal lain:

```bash
curl -s http://127.0.0.1:4723/status
```

Respons sehat memiliki `"ready": true`. Setelah itu hentikan server dengan
`Ctrl+C`, lalu jalankan seluruh port.

### 8.2 macOS / Linux — 20 port sekaligus

```bash
mkdir -p /tmp/appium-logs
for port in {4723..4742}; do
  nohup appium -p "$port" --log "/tmp/appium-logs/appium-$port.log" \
    >/dev/null 2>&1 &
done
```

Cek semua port:

```bash
for port in {4723..4742}; do
  printf '%s: ' "$port"
  curl -s "http://127.0.0.1:$port/status" | \
    python3 -c "import json,sys; print(json.load(sys.stdin)['value']['ready'])"
done
```

Untuk pertama kali, jalankan Appium bertahap 4–5 port per batch agar adb dan
USB controller tidak kewalahan.

### 8.3 Windows PowerShell — 20 port sekaligus

```powershell
$logDir = "C:\gads\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

4723..4742 | ForEach-Object {
  $port = $_
  Start-Job -Name "appium-$port" -ScriptBlock {
    param($p, $log)
    appium -p $p --log $log
  } -ArgumentList $port, (Join-Path $logDir "appium-$port.log")
}

Get-Job
```

Job PowerShell berhenti saat jendela PowerShell ditutup. Untuk operasi permanen,
gunakan Windows Task Scheduler atau service manager (mis. NSSM); log tetap
tersedia di `C:\gads\logs\appium-<port>.log`.

Jika jumlah HP bukan 20, jangan memakai seluruh rentang `4723..4742`. Setelah
`rack-01.json` dibuat (bagian 9), start Appium hanya untuk port yang ada di
manifest:

```powershell
$manifest = Get-Content .\rack-01.json -Raw | ConvertFrom-Json
$logDir = Join-Path $PWD "appium-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$manifest.devices | ForEach-Object {
  $port = $_.appium_port
  Start-Job -Name "appium-$port" -ScriptBlock {
    param($p, $log)
    appium -p $p --log $log
  } -ArgumentList $port, (Join-Path $logDir "appium-$port.log")
}
```

Cek semua port:

```powershell
4723..4742 | ForEach-Object {
  $status = Invoke-RestMethod "http://127.0.0.1:$_/status"
  [pscustomobject]@{ Port = $_; Ready = $status.value.ready }
} | Format-Table -AutoSize
```

Jika PowerShell tidak menemukan `appium`, buka terminal baru setelah
`npm install -g appium`, atau jalankan `where.exe appium` untuk melihat lokasi.

### 8.4 Hentikan Appium

macOS/Linux — hentikan berdasarkan port yang dipakai:

```bash
for port in {4723..4742}; do
  pkill -f "appium.*-p $port" 2>/dev/null || true
done
```

Windows PowerShell:

```powershell
$ports = 4723..4742
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in $ports } |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ }
```

> Appium boleh dijalankan tanpa menentukan device (-U); pemilihan HP dilakukan
> oleh capability `appium:udid` saat session dibuat. Satu server tetap harus
> unik per port.

## 9. Install aplikasi dan buat manifest

### 9.1 Install Python dependencies

Windows PowerShell:

```powershell
cd tools\social_media_bot
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS/Linux:

```bash
cd tools/social_media_bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Jika `.venv` berasal dari OS lain, hapus dan buat ulang.

### 9.2 Buat `rack-01.json`

> **Alur paling mudah:** lewati bagian ini. Jalankan dashboard tanpa file
> manifest (bagian 11), lalu isi device lewat **Setup rack → Scan ADB**.
> Dashboard otomatis membuat `rack-01.json` saat **Simpan & terapkan**.
>
> Bagian ini tetap berguna jika ingin membuat manifest dari CLI atau memakai
> template.

#### Otomatis dari ADB (disarankan untuk CLI)

Pastikan `adb devices` sudah bersih, lalu jalankan:

```bash
python generate_rack.py --output rack-01.json
```

Script akan:

- hanya mengambil device berstatus `device`;
- membuat port berurutan mulai `4723`;
- memberi nama `slot-01`, `slot-02`, …;
- melaporkan device `unauthorized`/`offline` yang dilewati;
- menolak menimpa file yang sudah ada (gunakan `--force` bila yakin).

Windows PowerShell:

```powershell
python generate_rack.py --output rack-01.json
```

Jika port awal ingin berbeda:

```bash
python generate_rack.py --output rack-01.json --first-port 4730
```

Setelah dibuat, **sesuaikan `device_name` dengan label fisik slot**. Urutan
`adb devices` belum tentu sama dengan urutan slot di rack.

#### Lewat UI (paling ramah pengguna)

Setelah dashboard berjalan, konfigurasi juga bisa diubah tanpa JSON:

1. Buka accordion **Setup rack** di panel Control kanan.
2. Klik **Scan ADB** untuk mengisi HP yang terdeteksi.
3. Edit nama slot, UDID, dan port Appium langsung di tabel.
4. Gunakan **+ Tambah device** atau tombol **×** untuk menambah/menghapus.
5. Klik **Simpan & terapkan**.

Konfigurasi disimpan ke manifest yang dipakai dashboard. Syaratnya: **tidak ada
device yang ter-connect** dan tidak ada job yang sedang berjalan. Jika host
Appium diubah, dashboard membuat bot baru otomatis.

> **Scan ADB** memakai `adb` di komputer tempat dashboard berjalan. Jika
> dashboard dipisah dari PC rack, buat manifest di PC rack dengan
> `generate_rack.py`, lalu salin ke PC dashboard atau isi device secara manual.

#### Manual dari template

Windows:

```powershell
Copy-Item rack-01.example.json rack-01.json
```

macOS/Linux:

```bash
cp rack-01.example.json rack-01.json
```

Buka `rack-01.json`, lalu sesuaikan **UDID**, **port**, dan **slot** dengan tabel
mapping ADB. Contoh potongan:

```json
{
  "rack": "rack-01",
  "host": "127.0.0.1",
  "devices": [
    {"udid": "R58M00000001", "appium_port": 4723, "device_name": "slot-01"},
    {"udid": "R58M00000002", "appium_port": 4724, "device_name": "slot-02"},
    {"udid": "R58M00000003", "appium_port": 4725, "device_name": "slot-03"}
  ]
}
```

Aturan penting:

- `udid` harus persis output `adb devices`;
- `appium_port` unik dan cocok dengan port server Appium;
- `device_name` dipakai sebagai label slot di dashboard;
- `host` = `127.0.0.1` bila Appium di PC yang sama; IP LAN PC rack bila remote;
- JSON boleh UTF-8 atau UTF-8 with BOM;
- jangan menyimpan password/secret di manifest yang di-commit ke Git.

Untuk 20 HP, contoh lengkap `rack-01.example.json` sudah berisi port
`4723..4742`; cukup ganti UDID.

### 9.3 Validasi manifest tanpa koneksi

```bash
python -c "from rack import load_rack; r = load_rack('rack-01.json'); print(r.name, len(r), r.device_map)"
```

Output harus menunjukkan `20` dan mapping UDID → port. Jika ada UDID/port
duplikat, aplikasi akan menolak manifest.

### 9.4 Jika memakai GADS

> **GADS tidak wajib** untuk setup rack lokal lewat USB seperti panduan ini.
> Lewati bagian ini jika Anda menjalankan Appium sendiri di PC server.

- Jika memakai **GADS remote**, HP tidak harus tersambung ke PC lokal; gunakan
  host/port/`server_path` dari GADS dan lewati langkah ADB lokal.
- Isi `server_path` per device bila endpoint Appium GADS memakai path khusus.
- Isi `client_secret` lewat environment `GADS_CLIENT_SECRET` atau flag
  dashboard `--client-secret`; jangan tulis di manifest publik.
- Detail kredensial: [docs/appium-credentials.md](../../docs/appium-credentials.md).

## 10. Tes koneksi pertama

Mulai kecil agar mudah mendiagnosis. Pastikan Appium untuk port terkait sudah
hidup dan minimal satu HP berstatus `device`.

Untuk tes **satu HP**, buat `rack-mini.json`:

```json
{
  "rack": "rack-mini",
  "host": "127.0.0.1",
  "devices": [
    {"udid": "UDID_HP_ANDA", "appium_port": 4723, "device_name": "slot-01"}
  ]
}
```

Lalu jalankan:

```bash
python run_rack.py --manifest rack-mini.json --workers 1
```

Jika satu HP berhasil, ganti manifest dengan `rack-01.json` sesuai jumlah HP
yang terpasang. Untuk tes awal beberapa HP, gunakan `--workers 2` lebih dulu.

```bash
python run_rack.py --manifest rack-01.json --workers 2
```

Output sehat kira-kira:

```text
Rack 'rack-01': 20/20 HP terhubung
  R58M00000001           port 4723   session ...
  R58M00000002           port 4724   session ...
```

Jika berhasil, jalankan semua worker:

```bash
python run_rack.py --manifest rack-01.json --workers 8
```

Mode `--hold` membuat session tetap terbuka sampai `Ctrl+C`:

```bash
python run_rack.py --manifest rack-01.json --workers 8 --hold
```

> Tanpa `--hold`, `run_rack.py` menutup semua session saat selesai. Untuk
> operasi berkelanjutan, gunakan dashboard. Jangan menjalankan `--hold` dan
> dashboard bersamaan pada manifest yang sama karena akan berebut session.

Arti status:

- `adb devices` = HP terdeteksi secara USB;
- Appium `/status` ready = server siap menerima session;
- `run_rack.py` sukses = session Appium per HP berhasil dibuat;
- dashboard **Online** = session Appium masih aktif dan bisa dikendalikan.

## 11. Jalankan dashboard dan integrasi

### 11.1 Start dashboard

```bash
python dashboard.py --workers 8
```

Default manifest: `rack-01.json`. Jika file belum ada, dashboard tetap start
dalam **mode setup** — buka `http://127.0.0.1:8080`, isi device lewat
**Setup rack**, lalu **Simpan & terapkan**. File manifest dibuat otomatis.

Jika ingin memakai nama manifest lain:

```bash
python dashboard.py --manifest rack-lab.json --workers 8
```

Buka `http://127.0.0.1:8080`.

Jika Appium berada di PC lain:

```bash
python dashboard.py --manifest rack-01.json \
  --appium-host 192.168.1.20 --workers 8
```

Windows PowerShell memakai backtick untuk lanjutan baris:

```powershell
python dashboard.py --manifest rack-01.json `
  --appium-host 192.168.1.20 --workers 8
```

Opsional langsung connect saat startup (hanya jika manifest sudah berisi device):

```bash
python dashboard.py --manifest rack-01.json --workers 8 --connect
```

### 11.2 Urutan verifikasi di dashboard

1. Buka dashboard, cek ringkasan koneksi di topbar.
2. Buka **Setup rack** → **Scan ADB** bila perlu menambah/mengoreksi device.
3. Klik **Simpan & terapkan** setelah slot/UDID/port benar.
4. Klik **Connect all**. Job masuk antrean; tunggu sampai selesai.
5. Cek strip device di bawah layar live: semua slot harus **Online**.
6. Klik satu kartu device → live screen muncul.
7. Pilih ukuran **Fit/Kecil/Sedang/Besar/1:1** sesuai kebutuhan.
8. Tes **tap** pada gambar, **swipe**, tombol Back/Home/Enter, dan input teks.
9. Buka accordion **Run action**, pilih platform + action + target.
10. Jalankan aksi pada **satu device** dulu; cek **Jobs** dan **Activity**.
11. Jika sudah stabil, jalankan target `All connected devices` atau bulk.
12. Klik **Stop** untuk menghentikan live stream (session tetap terbuka).
13. Selesai operasi: **Disconnect all**, lalu hentikan dashboard dengan `Ctrl+C`.

Gunakan model **B1**: dashboard tetap berjalan di PC server, PC lain hanya
membuka browser.

1. Cari IP LAN PC server:

```powershell
ipconfig
# lihat IPv4 Address, mis. 192.168.1.20
```

Atau:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike '127.*' } |
  Select-Object IPAddress, InterfaceAlias
```

2. Set token dashboard dan bind ke semua interface:

```powershell
$env:GADS_DASHBOARD_TOKEN = "ganti-dengan-token-panjang"
cd tools\social_media_bot
python dashboard.py --host 0.0.0.0 --port 8080
```

Token juga bisa diberikan lewat `--auth-token`. Bind non-loopback akan ditolak
jika token belum diisi.

Output akan menampilkan:

```text
Dashboard ready: http://127.0.0.1:8080
LAN: http://192.168.1.20:8080
```

3. Izinkan port 8080 di Windows Defender Firewall. Jalankan PowerShell sebagai
   Administrator:

```powershell
New-NetFirewallRule -DisplayName "GADS Dashboard 8080" `
  -Direction Inbound -Protocol TCP -LocalPort 8080 `
  -Action Allow -Profile Private
```

Saat pertama kali Windows menampilkan prompt firewall, pilih **Private
networks**, bukan Public.

4. Di PC lain, tes koneksi:

```powershell
Test-NetConnection 192.168.1.20 -Port 8080
Invoke-RestMethod http://192.168.1.20:8080/api/status
```

Lalu buka browser:

```text
http://192.168.1.20:8080
```

5. Pastikan kedua komputer berada di **subnet/WiFi yang sama** dan **AP/client
   isolation mati**. WiFi guest sering memblokir komunikasi antar-client.
6. Di **Setup rack**, Appium host tetap `127.0.0.1` karena Appium berjalan di PC
   server. Jangan mengganti ke IP LAN kecuali proses dashboard dipindah ke PC
   lain (model B2).
7. Gunakan IP statis/DHCP reservation agar alamat dashboard tidak berubah.

Catatan:

- Hanya port **8080** yang perlu dibuka untuk model B1. Port Appium
  `4723..4742` tetap lokal dan tidak perlu diekspos ke WiFi.
- Dashboard belum punya login. Jangan port-forward ke internet; untuk akses di
  luar rumah/kantor gunakan VPN/SSH tunnel.
- Streaming live screen memakai bandwidth WiFi. Pantau satu device pada satu
  waktu dan tekan **Stop** bila tidak diperlukan.
- Beberapa PC bisa membuka dashboard bersamaan, tetapi input live akan
  mengendalikan device yang sama.
- Pastikan PC server tidak sleep: atur **Power & sleep → Sleep = Never** dan
  matikan sleep untuk adaptor WiFi/USB.

### 11.3 Akses dashboard dari PC lain di WiFi yang sama

### 11.4 Scheduler, campaign, dan automation

**Scheduler**

- Di **Run action** atau **Campaign**, isi **Jadwal (opsional)**.
- Kosongkan untuk langsung; isi tanggal/jam untuk status `scheduled`.
- Job terjadwal disimpan di `<nama-manifest>.state.json` dan dipulihkan saat
  dashboard restart.
- Job `scheduled` bisa dibatalkan dari panel **Jobs**.

**Campaign**

- Buka accordion **Campaign**.
- Tulis teks, pilih platform yang mendukung text post, target, dan jeda.
- Campaign mengirim teks ke platform terpilih secara berurutan.
- Bisa dijadwalkan atau dibatalkan seperti job lain.

**Automation**

- **Batas harian**: atur follow/post/comment/like/save/reply per device.
  Nilai `0` memblokir aksi tersebut. Batas disimpan di state file.
- **Health check**: cek apakah Appium masih bisa mengakses device ter-connect.
  Ini bukan pengecekan login akun.
- **Analytics**: jumlah ok/gagal hari ini per platform dan action.
- **Notifikasi browser**: klik **Aktifkan notifikasi** dan izinkan di browser.
  Notifikasi biasanya hanya tersedia di `localhost` atau HTTPS, bukan HTTP LAN
  biasa.

**Like / save / reply**

- Tersedia di dropdown action untuk platform yang mendukung.
- Like/save membutuhkan URL postingan.
- Reply membutuhkan URL + teks balasan.
- Locator seperti `Like/Suka`, `Save/Simpan`, `Reply/Balas` bersifat generik;
  uji pada device dan versi aplikasi produksi sebelum dipakai massal.



## 12. Prosedur operasional harian

### 12.1 Saat menyalakan rack

1. Nyalakan **powered USB hub** dan tunggu stabil.
2. Nyalakan semua HP; tunggu sampai boot selesai.
3. Buka kunci layar semua HP; cek tidak ada dialog USB debugging yang tertahan.
4. Di PC rack:

```bash
adb devices -l
```

Pastikan semua HP (contoh: 20) berstatus `device`. Jika ada yang
`unauthorized`/`offline`, selesaikan dulu.

5. Pastikan Appium semua port ready (bagian 8).
6. Jalankan dashboard:

```bash
python dashboard.py --manifest rack-01.json --workers 8
```

7. Buka dashboard → **Connect all**.
8. Tunggu sampai semua kartu device **Online**.
9. Buka satu live screen sebagai smoke test, lalu tutup dengan **Stop**.

### 12.2 Saat beroperasi

- Jangan mencabut/memindahkan kabel saat session aktif.
- Pantau accordion **Activity** untuk error Appium atau timeout.
- Untuk job besar, gunakan **Bulk** dengan interval (mis. 3–10 detik) agar
  tidak menabrak rate limit platform.
- Jika satu HP bermasalah, jangan menghentikan seluruh rak; gunakan target
  device lain atau **Disconnect all → Connect all** setelah kabel diperbaiki.
- Live screen hanya untuk device yang dipilih; tekan **Stop** bila tidak
  dipantau agar screenshot Appium tidak membebani CPU.

### 12.3 Saat mematikan rack

1. Pastikan tidak ada job berjalan.
2. Klik **Disconnect all** di dashboard.
3. Hentikan dashboard dengan `Ctrl+C`.
4. Hentikan Appium (bagian 8.4).
5. Opsional: `adb kill-server` **setelah** semua session ditutup.
6. Matikan hub dan HP sesuai prosedur perangkat.

## 13. Troubleshooting

### 13.1 Tabel gejala umum

| Gejala | Cek | Solusi |
| --- | --- | --- |
| `adb devices` kosong | kabel, hub, driver, USB debugging | ganti kabel data, coba port lain, install driver OEM, aktifkan USB debugging |
| `unauthorized` | dialog RSA di HP | buka HP → Allow; jika tidak muncul, cabut/sambung atau revoke USB debugging authorizations |
| `offline` | koneksi USB tidak stabil | `adb reconnect`, ganti kabel/port/hub, hindari hub pasif |
| hanya sebagian HP terdeteksi | daya/bandwidth USB | bagi ke hub/controller lain, lepas perangkat yang tidak dipakai, cek adaptor hub |
| Appium `/status` gagal | server belum jalan/port bentrok | cek log Appium, `netstat`/`lsof`, ganti port |
| session gagal dibuat | Appium tidak melihat adb/device | jalankan `adb devices` di environment yang sama, `appium driver doctor uiautomator2` |
| dashboard Online padahal ADB device | host/port manifest salah | cocokkan `host` dan `appium_port`; coba `--appium-host` |
| live screen hitam/blank | session mati atau device sleep | cek status Appium, buka kunci HP, reconnect device |
| tap/swipe tidak akurat | ukuran/rotasi layar | pilih mode Fit/1:1, matikan auto-rotate, kalibrasi ulang |
| `Appium-Python-Client is not installed` | venv salah | aktifkan venv, `python -m pip install -r requirements.txt` |
| port 8080 dipakai | dashboard lain | `--port 8081` atau hentikan proses lama |
| dashboard remote tidak bisa connect | firewall/LAN | izinkan port Appium, cek IP, coba `curl http://IP:4723/status` dari PC controller |

### 13.2 `adb` mengenali HP, tetapi Appium gagal

1. Pastikan Appium dijalankan dari terminal yang sama yang bisa menjalankan
   `adb devices`.
2. Jalankan:

```bash
appium driver doctor uiautomator2
```

3. Cek log Appium:

```bash
tail -n 100 /tmp/appium-logs/appium-4723.log
```

Windows:

```powershell
Get-Content C:\gads\logs\appium-4723.log -Tail 100
```

4. Pastikan tidak ada dua Appium memakai port yang sama.
5. Coba satu server + satu HP lebih dulu:

```bash
appium -p 4723
python run_rack.py --manifest rack-01.json --workers 1
```

### 13.3 Device tiba-tiba hilang di tengah job

1. Cek `adb devices`; jika hilang, perbaiki kabel/hub.
2. Di dashboard klik **Disconnect all**, lalu **Connect all**.
3. Untuk satu device, API/CLI reconnect tersedia lewat `SocialMediaBot.reconnect_device`;
   dashboard memakai connect ulang seluruh rack.
4. Jika sering terjadi, turunkan jumlah worker, pisahkan hub, atau ganti kabel.

### 13.4 Aplikasi media sosial logout / checkpoint

- Login manual ulang di HP tersebut.
- Selesaikan captcha/2FA.
- Pastikan `no_reset` tidak diubah menjadi full reset.
- Jika akun terkena checkpoint, hentikan otomasi akun itu; jangan memaksa
  request berulang.

## 14. Keamanan dan kepatuhan

- Dashboard **memakai token login** jika `GADS_DASHBOARD_TOKEN`/`--auth-token`
  diisi. Bind non-loopback wajib memakai token. Default lokal `127.0.0.1` tidak
  meminta token.

- Untuk akses remote, gunakan SSH tunnel/reverse proxy HTTPS; token yang dikirim
  lewat HTTP biasa tidak terenkripsi. Contoh SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 user@pc-rack
```

lalu buka `http://127.0.0.1:8080` di PC controller.

- Appium juga tanpa autentikasi. Batasi akses ke LAN tepercaya dan firewall;
  jangan expose port `4723..4742` ke publik.
- Simpan `client_secret`/token di environment variable, bukan di Git.
- Satu akun media sosial untuk satu HP; hindari login akun yang sama di banyak
  device secara paralel.
- Hormati ketentuan layanan platform. Gunakan jeda acak, interval bulk yang
  wajar, dan hentikan otomasi bila akun terkena captcha/checkpoint.
- Jangan memakai `fullReset` di farm produksi karena dapat menghapus data login
  aplikasi.
- Batasi akses fisik ke rack; HP yang terbuka bisa mengakses akun tanpa
  otorisasi.

## 15. Checklist acceptance

Centang sebelum rack dianggap siap produksi.

### Hardware & Android

- [ ] Semua HP terpasang di powered hub dan diberi label slot.
- [ ] USB debugging aktif di semua HP.
- [ ] Semua HP sudah approve **Allow USB debugging**.
- [ ] Layar tidak terkunci, auto-rotate mati, stay-awake aktif.
- [ ] Akun media sosial sudah login manual di setiap HP.
- [ ] Kabel data dan adaptor hub stabil; tidak ada HP yang restart sendiri.

### ADB

- [ ] `adb version` berjalan di PC rack.
- [ ] `adb devices -l` menampilkan **20** device berstatus `device`.
- [ ] Mapping slot ↔ UDID dicatat.
- [ ] Tidak ada status `unauthorized` atau `offline`.

### Appium

- [ ] Node.js LTS dan Appium terpasang.
- [ ] Driver `uiautomator2` terpasang.
- [ ] Port `4723..4742` listening.
- [ ] Semua endpoint `/status` merespons `"ready": true`.
- [ ] Log Appium tersedia dan bisa dibaca.

### Aplikasi

- [ ] `rack-01.json` berisi UDID yang benar sesuai jumlah HP dan port unik.
- [ ] Validasi manifest menampilkan jumlah device yang sesuai.
- [ ] `run_rack.py --workers 2` berhasil.
- [ ] `run_rack.py --workers 8` berhasil untuk seluruh HP yang terpasang.
- [ ] Dashboard menampilkan semua device **Online**.
- [ ] **Setup rack → Scan ADB** menemukan HP dan **Simpan & terapkan** berhasil.
- [ ] Mengubah satu slot/UDID/port lewat UI tersimpan ke manifest.
- [ ] Live screen tampil dan tap/swipe/teks berfungsi.
- [ ] Satu job follow/post/comment pada satu device berhasil.
- [ ] Aksi like/save/reply berhasil pada satu device uji.
- [ ] Scheduler: job `scheduled` muncul dan berjalan pada waktunya.
- [ ] Campaign text post berhasil ke minimal dua platform.
- [ ] Daily limit, health check, dan analytics tampil di accordion Automation.
- [ ] Bulk campaign kecil berhasil dan progress/cancel berfungsi.
- [ ] Restart dashboard + Connect all memulihkan seluruh rack.

### Keamanan

- [ ] Dashboard hanya di localhost/LAN tepercaya.
- [ ] Port Appium tidak terbuka ke internet.
- [ ] Secret tidak tersimpan di manifest/Git.
- [ ] Interval bulk dan volume aksi sesuai batas platform.

## 16. Referensi cepat

| Kebutuhan | Perintah |
| --- | --- |
| Cek HP | `adb devices -l` |
| Cek Appium | `curl http://127.0.0.1:4723/status` |
| Generate manifest | `python generate_rack.py --output rack-01.json` |
| Validasi manifest | `python -c "from rack import load_rack; print(len(load_rack('rack-01.json')))"` |
| Connect CLI | `python run_rack.py --manifest rack-01.json --workers 8` |
| Dashboard | `python dashboard.py` |
| Test | `python -m unittest discover -s tests -v` |
| Install requirements | `python -m pip install -r requirements.txt` |

Dokumen terkait:

- [README SocialMediaBot](README.md) — API, platform, dashboard, GADS.
- [generate_rack.py](generate_rack.py) — membuat manifest dari output ADB.
- [docs/appium-credentials.md](../../docs/appium-credentials.md) — kredensial
  Appium/GADS.
- [rack-01.example.json](rack-01.example.json) — template 20 HP port
  `4723..4742`.
- [docs/adb-tunnel.md](../../docs/adb-tunnel.md) — tunnel ADB untuk akses
  remote, bila digunakan.

> Jika ragu, ulangi dari tes kecil: **1 HP → 1 Appium → 1 session → dashboard
> 1 device**. Setelah itu baru naikkan ke seluruh rack.
