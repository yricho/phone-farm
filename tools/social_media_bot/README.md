# SocialMediaBot — Kerangka Otomasi Phone Farm (Appium + Python)

Kerangka class `SocialMediaBot` untuk mengendalikan satu rak phone farm
(contoh: **20 HP Android**) sekaligus lewat Appium. Mendukung **Facebook,
Instagram, Threads, X, dan TikTok** lewat registry platform dengan capability
matrix (follow, post teks, comment) dan live screen per device di dashboard.
Satu HP = satu Appium server (satu port), semua session disimpan dalam satu
objek berdasarkan UDID.

> **Baru pertama kali?** Ikuti panduan lengkap dari perakitan rack, setting
> Android, ADB, Appium per port, sampai dashboard:
> **[RACK_SETUP.md](RACK_SETUP.md)**.
>
> **Urutan menjalankan harian (Windows):** **[RUNBOOK.md](RUNBOOK.md)** —
> env → Appium → verifikasi → dashboard → Connect all.

> **Status MVP dan urutan pekerjaan:** lihat
> **[MVP_PRIORITIES.md](MVP_PRIORITIES.md)**.
> **Acceptance device nyata:** lihat
> **[MVP_ACCEPTANCE.md](MVP_ACCEPTANCE.md)**.

## Struktur

```
tools/social_media_bot/
├── __init__.py                 # re-export public API
├── social_media_bot.py         # SocialMediaBot, DeviceConnection, DeviceTaskResult
├── facebook.py                 # aksi Facebook (legacy, stabil)
├── models.py                   # FollowResult, PostResult, CommentResult
├── errors.py                   # hierarki exception bersama
├── platforms/                  # registry + adapter 5 platform
│   ├── __init__.py             # default_platforms, platform_catalog
│   ├── base.py                 # engine flow Appium generik
│   ├── facebook.py             # wrapper facebook.py
│   ├── instagram.py
│   ├── threads.py
│   ├── x.py
│   └── tiktok.py
├── rack.py                     # DeviceSpec, Rack, load_rack, sequential_rack
├── generate_rack.py            # buat manifest dari `adb devices -l`
├── console.py                  # helper encoding console (Windows-safe)
├── dashboard.py                # server HTTP + job manager dashboard
├── dashboard_ui/               # index.html, styles.css, app.js
├── diagnose.py                 # cek manifest -> adb -> Appium per port
├── dump_ui.py                  # dump page source Appium (cari locator UI)
├── start-appium.bat            # Windows: satu window Appium per port
├── start-dashboard.bat         # Windows: jalankan dashboard (pakai .venv)
├── run_rack.py                 # CLI: connect 1 rak dari manifest JSON
├── RACK_SETUP.md               # panduan fisik → ADB → Appium → dashboard
├── RUNBOOK.md                  # urutan singkat menjalankan farm harian
├── PERFORMANCE.md              # cara kerja live screen adb, tuning jeda, fast mode
├── MVP_PRIORITIES.md           # prioritas P0/P1/P2 dan checklist rilis
├── MVP_ACCEPTANCE.md           # checklist acceptance device nyata
├── rack-01.example.json        # contoh manifest 20 HP (port 4723-4742)
├── example_multi_device.py     # contoh singkat 4 HP
├── requirements.txt
├── README.md
└── tests/
    ├── test_social_media_bot.py
    ├── test_rack.py
    ├── test_facebook.py
    ├── test_platforms.py
    ├── test_console.py
    ├── test_generate_rack.py
    └── test_dashboard.py
```

## Kompatibilitas platform

Aplikasi berjalan di **Windows 10/11, macOS, dan Linux** dengan **Python 3.9+**.
Kode tidak memakai `fcntl`, `termios`, `os.fork`, signal POSIX, atau path
`/usr/...`; path memakai `pathlib` dan server memakai `http.server` dari
stdlib. Khusus Windows:

- perintah Python memakai `py -3` atau `python` (bukan `python3`);
- console otomatis di-set UTF-8 agar log berisi `·`, `…`, dan teks Indonesia
  tidak error di code page lama;
- manifest JSON boleh disimpan sebagai **UTF-8 with BOM** (default Notepad);
- import bulk `.txt`/`.csv` juga otomatis membuang BOM dari baris pertama.

## Instalasi

### Windows — PowerShell

```powershell
cd tools\social_media_bot
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Jika PowerShell menolak aktivasi script, jalankan sekali untuk sesi itu:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Windows — Command Prompt (cmd)

```bat
cd tools\social_media_bot
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### macOS / Linux

```bash
cd tools/social_media_bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

> Setelah venv aktif, gunakan `python` di semua platform. Di macOS/Linux,
> `python3` juga tersedia; di Windows gunakan `python` atau `py -3`.

Appium server 2.x/3.x + driver `uiautomator2` (butuh **Node.js LTS**).
Perintahnya sama di Windows, macOS, dan Linux:

```bash
appium driver install uiautomator2
appium -p 4723   # HP slot 01
appium -p 4724   # HP slot 02
# ... sampai -p 4742 untuk 20 HP
```

Di Windows, buka terminal baru untuk setiap port, atau pakai helper (satu window
`cmd` per port, aman terhadap execution policy PowerShell):

```bat
start-appium.bat                          :: 4723 4724 4725
start-appium.bat 4723 4724 4725 4726      :: daftar port sendiri
```

Alternatif PowerShell (background jobs):

```powershell
1..20 | ForEach-Object {
  $port = 4722 + $_
  Start-Job -Name "appium-$port" -ScriptBlock { param($p) appium -p $p } -ArgumentList $port
}
Get-Job
```

Jika muncul `appium.ps1 cannot be loaded because running scripts is disabled`,
lihat **Troubleshooting Windows** di bagian bawah.

Pastikan `adb devices` menampilkan semua HP (contoh: 20) dengan status `device`
sebelum menjalankan bot. Di Windows, tambahkan folder **Android platform-tools**
ke `PATH` agar `adb` dikenali. Untuk farm, gunakan USB hub bertenaga (powered
hub) dan jangan menjalankan `adb kill-server` saat session aktif.

## Konsep rak 20 HP

- **Port**: 20 HP memakai port berurutan `4723..4742` (lihat
  `rack-01.example.json`). Port harus unik per HP karena tiap HP dilayani
  proses Appium sendiri.
- **Host**: satu host Appium (mis. `192.168.1.20`) dipakai semua HP; yang
  membedakan hanya port.
- **Paralelisme**: `connect_devices()` meng connect HP secara paralel
  (default 8 worker, bisa diatur via `max_workers=` atau `SocialMediaBot(max_workers=...)`).
- **Isolasi kegagalan**: `continue_on_error=True` membuat 18 HP tetap terhubung
  walau 2 HP gagal; daftar HP yang gagal tetap bisa dihitung dari hasil return.
- **Lock per UDID**: connect/reconnect/disconnect untuk HP yang sama
  diserialisasi, sehingga aman dipanggil dari beberapa thread worker.

## Manifest rak

`rack-01.json`:

```json
{
  "rack": "rack-01",
  "host": "192.168.1.20",
  "devices": [
    {"udid": "R58M00000001", "appium_port": 4723, "device_name": "slot-01"},
    {"udid": "R58M00000002", "appium_port": 4724, "device_name": "slot-02"}
  ]
}
```

- `udid` dan `appium_port` wajib; `port` diterima sebagai alias.
- Key lain (`device_name`, `platform_version`, `app_package`, `app_activity`,
  `no_reset`, `new_command_timeout`, `server_path`, `implicit_wait`,
  `extra_capabilities`) diteruskan ke `connect_device()` khusus HP tersebut.
- UDID dan port duplikat ditolak saat manifest dibaca.

### Generate manifest otomatis dari ADB

Daripada menyalin UDID manual, buat `rack-01.json` dari HP yang sedang
terhubung:

```bash
cd tools/social_media_bot
python generate_rack.py --output rack-01.json
```

Script hanya mengambil device berstatus `device`, memberi port berurutan mulai
`4723`, dan menamai slot `slot-01`, `slot-02`, dst. Device
`unauthorized`/`offline` akan dilaporkan dan dilewati. File lama tidak ditimpa
tanpa `--force`.

Windows PowerShell:

```powershell
python generate_rack.py --output rack-01.json
```

Jumlah port mengikuti jumlah HP yang terdeteksi. Setelah generate, sesuaikan
`device_name` dengan label fisik slot di rack.

Jalankan seluruh rak.

macOS / Linux:

```bash
cd tools/social_media_bot
python3 run_rack.py --manifest rack-01.example.json --workers 8
python3 run_rack.py --manifest rack-01.json --workers 10 --hold   # biarkan terbuka
```

Windows PowerShell / cmd:

```powershell
cd tools\social_media_bot
python run_rack.py --manifest rack-01.example.json --workers 8
python run_rack.py --manifest rack-01.json --workers 10 --hold
```

Path absolut Windows juga didukung; bungkus dengan tanda kutip:

```powershell
python run_rack.py --manifest "C:\gads\rack-01.json"
```

Manifest boleh memakai UTF-8 biasa maupun **UTF-8 with BOM**.

Output ringkas: jumlah HP terhubung, port, session id, dan daftar HP yang gagal.
Exit code `0` bila semua terhubung, `1` bila ada yang gagal, `2` bila manifest
atau Appium bermasalah.

## Pemakaian cepat (tanpa manifest)

```python
from social_media_bot import SocialMediaBot, sequential_rack

bot = SocialMediaBot(host="192.168.1.20", max_workers=8)

# 20 HP dengan port 4723..4742
rack = sequential_rack(
    "rack-01",
    [f"R58M{index:08d}" for index in range(1, 21)],
    first_port=4723,
)

try:
    connected = bot.connect_devices(rack, continue_on_error=True)
    print(f"{len(connected)}/{len(rack)} HP terhubung")

    # satu HP spesifik
    driver = bot.get_driver("R58M00000001")
    print(driver.session_id)
finally:
    bot.disconnect_all()
```

Atau satu per satu sesuai signature awal:

```python
bot.connect_device("R58M00000001", 4723)
bot.connect_device("R58M00000002", 4724, app_package="com.instagram.android")
bot.disconnect_all()
```

Semua session juga otomatis ditutup bila memakai context manager:

```python
with SocialMediaBot(host="192.168.1.20") as bot:
    bot.connect_devices(rack)
    ...
```

## Aksi massal di 20 HP

`run_on_devices()` menjalankan satu callable `(driver, udid)` secara paralel dan
mengembalikan hasil/exception per HP tanpa menghentikan HP lain:

```python
results = bot.run_on_devices(
    lambda driver, udid: driver.activate_app("com.instagram.android"),
    max_workers=8,
)

failed = [udid for udid, result in results.items() if not result.ok]
for udid in failed:
    print(udid, "->", results[udid].error)
```

Gunakan `raise_on_error=True` bila ingin error pertama langsung dilempar setelah
semua HP selesai diproses.

## Platform yang didukung

Registry `platforms/` menyatukan 5 jaringan dengan capability matrix. Aksi yang
tidak didukung tidak muncul sebagai pilihan di dashboard dan ditolak server
(HTTP 400).

| Platform | Key | Package | Follow | Post teks | Comment | Like / Save / Reply |
| --- | --- | --- | --- | --- | --- | --- |
| Facebook | `facebook` | `com.facebook.katana` | ✅ | ✅ | ✅ | ✅ |
| Instagram | `instagram` | `com.instagram.android` | ✅ | ❌ butuh media | ✅ | ✅ |
| Threads | `threads` | `com.instagram.barcelona` | ✅ | ✅ maks 500 char | ✅ | ✅ |
| X | `x` | `com.twitter.android` | ✅ | ✅ maks 280 char | ✅ (reply) | ✅ |
| TikTok | `tiktok` | `com.zhiliaoapp.musically` | ✅ | ❌ butuh video | ✅ | ✅ |

Catatan:

- Instagram dan TikTok **tidak mendukung posting teks murni**; posting butuh
  flow pemilihan media/upload yang terpisah. Karena itu aksi `post` sengaja
  ditolak untuk kedua platform tersebut. **Keputusan MVP:** media posting
  (foto/video) berada di luar scope sampai ada flow upload khusus.
- Package TikTok dapat berbeda per region (`com.ss.android.ugc.trill`); ubah
  `package` pada `platforms/tiktok.py` bila perlu.
- Semua adapter memakai jeda acak **3–7 detik** antar langkah dan mengembalikan
  model yang sama (`FollowResult`, `PostResult`, `CommentResult`,
  `EngagementResult`).
- Locator **like/save/reply** bersifat generik (Like/Suka, Save/Simpan,
  Reply/Balas). Verifikasi ulang di device dan versi aplikasi produksi sebelum
  dipakai massal.

Pemakaian langsung:

```python
from social_media_bot import default_platforms

platforms = default_platforms()

platforms["instagram"].follow_user(driver, "target.account")
platforms["threads"].create_post(driver, "Halo Threads")
platforms["x"].comment_on_post(
    driver, "https://x.com/example/status/123", "Halo X"
)
```

Atau lewat bot multi-device:

```python
platforms = default_platforms()
ig = platforms["instagram"]

results = bot.run_on_devices(
    lambda driver, udid: ig.follow_user(driver, "target.account"),
    max_workers=8,
)
```

Facebook tetap kompatibel: `follow_user`, `create_post`, dan `comment_on_post`
di `facebook.py` masih bisa dipanggil langsung; `FacebookPlatform` hanya
membungkusnya agar seragam dengan platform lain.

## Follow user di Facebook

`follow_user(driver, target_username)` membuka Facebook, mencari username,
membuka profilnya, lalu menekan tombol **Follow**. Di antara setiap langkah
diberi jeda acak **3–7 detik** (default) agar alurnya tidak terlalu cepat.

```python
from social_media_bot import SocialMediaBot, follow_user

bot = SocialMediaBot(host="192.168.1.20")
try:
    bot.connect_device("R58M00000001", 4723)
    driver = bot.get_driver("R58M00000001")

    result = follow_user(driver, "target.account")
    print(result.username, result.status)   # followed / already_following
    print(result.followed)                  # True bila benar-benar menekan Follow
finally:
    bot.disconnect_all()
```

Untuk satu rak sekaligus, kombinasikan dengan `run_on_devices()`:

```python
results = bot.run_on_devices(
    lambda driver, udid: follow_user(driver, "target.account", logger=logger),
    max_workers=8,
)

for udid, task in results.items():
    if task.ok:
        print(udid, "->", task.result.status)
    else:
        print(udid, "gagal:", task.error)
```

Parameter `follow_user`:

| Parameter | Default | Keterangan |
| --- | --- | --- |
| `driver` | wajib | Driver Appium, mis. `bot.get_driver(udid)` |
| `target_username` | wajib | Username/handle, boleh diawali `@` |
| `delay_range` | `(3.0, 7.0)` | Rentang jeda acak antar langkah, dalam detik |
| `timeout` | `20.0` | Batas tunggu tiap elemen UI |
| `package` | `com.facebook.katana` | Ganti ke `com.facebook.lite` untuk FB Lite |
| `logger` | module logger | Logger opsional; UDID sebaiknya dicantumkan di luar |

Alur yang dijalankan:

1. `activate_app` + tunggu app masuk foreground
2. buka ikon/kotak pencarian (`Search`/`Cari`)
3. ketik username lalu tekan Enter
4. buka tab `People`/`Orang` bila ada
5. tap hasil profil yang cocok
6. tekan `Follow`/`Ikuti`, atau laporkan `already_following` bila tombol
   `Following`/`Mengikuti` sudah tampil

Nilai balik `FollowResult` berisi `username`, `status`
(`"followed"`/`"already_following"`), `button_text`, dan properti `followed`.
Jika satu langkah gagal, `FacebookAutomationError` dilempar dengan konteks
username dan package.

**Catatan:** selektor UI Facebook mudah berubah. Kalau flow gagal setelah
update aplikasi, sesuaikan daftar locator di bagian bawah `facebook.py`
(`_search_entry_locators`, `_search_box_locators`, `_follow_locators`, dst.).
`follow_user` mengandalkan teks hasil pencarian yang memuat username, jadi
pastikan handle yang dicari muncul di hasil `People`/`Orang`. Tetap patuhi
ketentuan layanan Facebook saat mengotomatiskan akun.

## Buat postingan di Facebook

`create_post(driver, post_text)` membuka Facebook, menekan composer
"What's on your mind?", mengetikkan `post_text`, lalu menekan tombol **Post**.
Jeda acak **3–7 detik** juga diterapkan di antara langkah.

```python
from social_media_bot import create_post

result = create_post(driver, "Halo dari phone farm!\n#otomasi")
print(result.status)      # posted
print(result.posted)      # True
print(result.post_text)   # teks yang diketik
```

Untuk satu rak sekaligus:

```python
results = bot.run_on_devices(
    lambda driver, udid: create_post(driver, "Halo dari rak 01"),
    max_workers=8,
)
failed = [udid for udid, task in results.items() if not task.ok]
```

Parameter `create_post`:

| Parameter | Default | Keterangan |
| --- | --- | --- |
| `driver` | wajib | Driver Appium, mis. `bot.get_driver(udid)` |
| `post_text` | wajib | Teks postingan; tidak boleh kosong, maks 63.206 karakter, newline dipertahankan |
| `delay_range` | `(3.0, 7.0)` | Rentang jeda acak antar langkah, dalam detik |
| `timeout` | `20.0` | Batas tunggu tiap elemen UI |
| `package` | `com.facebook.katana` | Ganti ke `com.facebook.lite` untuk FB Lite |
| `logger` | module logger | Logger opsional |

Alur yang dijalankan:

1. bangunkan layar (`KEYCODE_WAKEUP` 224) + `activate_app` + tunggu app
   foreground. Kalau HP masih terkunci, bot mencoba swipe keyguard dan menulis
   peringatan; status lock juga ikut di pesan error kalau posting gagal.
2. cari pill composer `What's on your mind` / `Apa yang Anda pikirkan` /
   `Bagikan sesuatu` / `Tulis sesuatu` / `Buat postingan` / `Create post`.
   Kalau layar composer dari percobaan sebelumnya masih terbuka, field-nya
   langsung dipakai; kalau pill tidak terlihat, bot membuka **`fb://feed`**,
   menutup popup “Nanti”/“Not now”, lalu scroll feed ke atas sebelum menyerah —
   jadi layar sisa run sebelumnya tidak langsung jadi timeout 20 detik.
3. tunggu editor muncul (build baru membuka halaman terpisah
   **“Postingan baru” / “Buat postingan baru”**), lalu **tap field tersebut**
   agar fokus. Kalau editor belum muncul, composer di-tap ulang dan field yang
   sedang fokus dipakai, jadi langkah ini juga tidak langsung timeout.
4. ketik `post_text` di editor
5. sembunyikan keyboard bila perlu
6. tombol final `Post` / `Posting` / `Bagikan` / `Kirim` / `Share`, termasuk
   label terbungkus (`Posting sekarang`, `Posting sebagai …`). Judul layar
   `Postingan baru` tidak pernah dianggap tombol. Kandidat yang
   `clickable="false"` dicoba lewat tap koordinat. Pada Facebook **588+**
   alurnya dua langkah: tombol **`Berikutnya`** di layar pertama, lalu tombol
   **`Posting`** di layar pengaturan postingan — keduanya ditangani otomatis.

Nilai balik `PostResult` berisi `post_text`, `status` (`"posted"`),
`button_text`, dan properti `posted`. Jika satu langkah gagal,
`FacebookAutomationError` dilempar dengan konteks package.

**Saat gagal (diagnosa):** page source dan screenshot layar otomatis disimpan,
lalu path-nya ditempel di pesan error bersama konteks device, mis.
`Timed out after 20s waiting for the post composer (current_package=com.facebook.katana; current_activity=.LoginActivity; locked=True; visible=Masuk ke Facebook | …; UI dump: …xml, …png)`.
Konteks `locked`/`visible` langsung menunjukkan apakah HP terkunci, kena
login/checkpoint, atau ada dialog lain. Kirim file XML itu untuk analisis
locator. Set `GADS_UI_DUMP_DIR` bila ingin menyimpan dump di folder lain.

**Hanya sebagian slot yang gagal (versi Facebook sama):** alur sekarang
membangunkan layar lebih dulu dan **mengulang sekali** kalau gagal sebelum
tombol Post ditekan (tidak akan membuat posting dobel). Kalau masih ada slot
yang gagal, baca konteks di pesan error:

- `locked=True` → HP terkunci/PIN; matikan lock screen atau unlock manual;
- `current_activity` bukan `com.facebook.katana` → ada app/dialog lain di depan;
- `visible=Masuk ke Facebook` / `Konfirmasi identitas` → akun kena login/checkpoint;
- `visible=` menampilkan dialog/popup lain → tutup manual, lalu ulangi;
- slot yang sedang dibuka **live view**-nya juga lebih lambat karena stream
  memakai sesi Appium yang sama — tekan **Stop** dulu saat mengetes slot itu.

**Catatan:** tombol Post tidak akan tertukar dengan editor walaupun isi
postingan persis `"Post"` (kandidat `EditText` difilter). Pill composer di feed
(“Apa yang Anda pikirkan?”) juga sering berupa `EditText`; pill tersebut
dikecualikan berdasarkan **element id** Appium (fallback ke
`resource-id`/`bounds` bila id tidak tersedia) sehingga teks tidak pernah
diketik ke feed, sementara editor baru yang kebetulan punya `bounds` sama tetap
ditemukan. Locator composer dan tombol Post ada di
`_composer_prompt_locators()` / `_post_button_locators()` pada `facebook.py`
bila perlu disesuaikan.

Kalau versi Facebook di HP berbeda (mis. 588.x) dan langkah di atas masih
timeout, buka dulu file `UI dump: …xml` yang otomatis disimpan saat gagal (path
ada di pesan error), atau dump manual hierarki layarnya dengan `dump_ui.py`
(lihat bagian **Dump UI**), lalu sesuaikan locator berdasarkan
`class`/`text`/`content-desc` yang muncul.

## Komentari postingan Facebook

`comment_on_post(driver, target_url, comment_text)` membuka link postingan
**di dalam aplikasi Facebook**, membuka kotak komentar, mengetik komentar, lalu
mengirimnya. Jeda acak **3–7 detik** diterapkan antar langkah.

```python
from social_media_bot import comment_on_post

result = comment_on_post(
    driver,
    "https://www.facebook.com/example/posts/1234567890",
    "Nice post!\n#otomasi",
)
print(result.commented)     # True
print(result.status)        # commented
print(result.button_text)   # Send / Kirim / IME_SEND / ENTER
```

Untuk satu rak:

```python
results = bot.run_on_devices(
    lambda driver, udid: comment_on_post(driver, post_url, "Halo dari farm"),
    max_workers=8,
)
```

Parameter `comment_on_post`:

| Parameter | Default | Keterangan |
| --- | --- | --- |
| `driver` | wajib | Driver Appium, mis. `bot.get_driver(udid)` |
| `target_url` | wajib | URL postingan `http(s)://...` atau deep link `fb://...` |
| `comment_text` | wajib | Isi komentar; tidak boleh kosong, maks 8.000 karakter, newline dipertahankan |
| `delay_range` | `(3.0, 7.0)` | Rentang jeda acak antar langkah, dalam detik |
| `timeout` | `20.0` | Batas tunggu tiap elemen UI |
| `package` | `com.facebook.katana` | Ganti ke `com.facebook.lite` untuk FB Lite |
| `logger` | module logger | Logger opsional |

Cara membuka link:

1. `mobile: deepLink` (`url` + `package`) — jalur utama agar link masuk ke app.
2. `mobile: startActivity` dengan `action=android.intent.action.VIEW`,
   `uri`, dan `package` — fallback bila deep link gagal.
3. `driver.get(url)` — fallback terakhir (mis. driver non-UiAutomator2).

Setelah itu bot memastikan Facebook berada di foreground. Jika browser yang
terbuka, fungsi berhenti dengan `FacebookAutomationError`; gunakan URL app-link
Facebook atau deep link `fb://post/<id>`.

Alur komentar:

1. buka URL postingan + tunggu Facebook foreground
2. cari kotak komentar; jika belum tampil, tap tombol `Comment`/`Komentari`
3. fokus ke editor, ketik `comment_text`
4. kirim lewat tombol `Send`/`Kirim`; jika tidak ada, `mobile: performEditorAction`
   (`send`), lalu fallback tombol Enter

Nilai balik `CommentResult` berisi `target_url`, `comment_text`, `status`
(`"commented"`), `button_text`, dan properti `commented`. Locator komentar ada
di `_comment_button_locators()`, `_comment_editor_locators()`, dan
`_send_button_locators()` pada `facebook.py`.

## Recovery HP yang drop

```python
try:
    bot.reconnect_device("R58M00000007", attempts=3, delay=2.0)
except DeviceConnectionError:
    # semua percobaan gagal: HP perlu di-connect ulang secara manual
    bot.connect_device("R58M00000007", 4729)
```

`reconnect_device()` memakai port, server URL, capabilities, dan implicit wait
yang tersimpan, jadi tidak perlu mengulang konfigurasi awal.

## Dashboard UI (web)

Dashboard lokal untuk memantau dan mengendalikan satu rak tanpa CLI. Server
memakai **Python stdlib** (`http.server`), UI-nya HTML/CSS/JS tanpa build step
dan tanpa dependency tambahan.

```bash
cd tools/social_media_bot
python3 dashboard.py
# default manifest: rack-01.json
# jika file belum ada, dashboard masuk mode setup — lengkapi lewat UI
```

Windows (PowerShell atau cmd):

```powershell
cd tools\social_media_bot
python dashboard.py
```

Atau langsung `start-dashboard.bat` — script memilih `.venv\Scripts\python.exe`
bila ada, memberi catatan bila `rack-01.json` belum ada (dashboard masuk mode
setup), dan meneruskan argumen tambahan (mis. `start-dashboard.bat --workers 4`).

Jika ingin memakai nama manifest lain:

```bash
python3 dashboard.py --manifest rack-lab.json
```

Opsi lain — **akses dari PC lain di WiFi/LAN** (dashboard tetap di PC server).
Bind non-loopback sekarang **wajib memakai token**:

```bash
export GADS_DASHBOARD_TOKEN="ganti-dengan-token-panjang"
python3 dashboard.py --host 0.0.0.0 --port 8080
```

Token juga bisa diberikan lewat `--auth-token`. Buka `http://IP-PC-SERVER:8080`
dari PC lain, lalu masukkan token pada halaman login. Appium host di **Setup
rack** tetap `127.0.0.1`; hanya port dashboard `8080` yang perlu dibuka di
firewall.

Jika **proses dashboard dipindah** ke PC lain dan Appium ada di PC server:

```bash
python3 dashboard.py --manifest rack-01.json \
  --appium-host 192.168.1.20 --workers 8
```

Model ini memerlukan semua port Appium (`4723..4742`) terbuka di LAN dan lebih
berisiko karena Appium tidak punya autentikasi.

Windows PowerShell memakai backtick untuk lanjutan baris:

```powershell
python dashboard.py --manifest rack-01.json `
  --appium-host 192.168.1.20 --workers 8
```

- `--connect` langsung mengantrekan job connect seluruh rak saat startup.
- Untuk membuka dashboard dari PC lain di WiFi yang sama, set
  `GADS_DASHBOARD_TOKEN` (atau `--auth-token`), jalankan dengan `--host 0.0.0.0`,
  izinkan port `8080` di firewall **Private**, lalu buka
  `http://IP-PC-SERVER:8080` dan masukkan token. Langkah lengkap ada di
  [RACK_SETUP.md](RACK_SETUP.md) bagian 11.3.
- `--client-secret` atau env `GADS_CLIENT_SECRET` untuk endpoint GADS.
  PowerShell: `$env:GADS_CLIENT_SECRET = "secret"`; cmd:
  `set GADS_CLIENT_SECRET=secret`.
- Secara default hanya listen di `127.0.0.1` dan tidak meminta token. Bind ke
  alamat non-loopback **ditolak** bila token belum diisi. Token dikirim lewat
  HTTP, jadi untuk akses remote tetap gunakan SSH tunnel/reverse proxy HTTPS.
  Saat memakai `--host 0.0.0.0` di Windows, izinkan port tersebut di Windows
  Defender Firewall.

Fitur:

- **live screen** selalu tampil di atas dashboard; pilih HP lewat **strip kartu
  horizontal** di bawah layar (slot, UDID, status Online/Offline, port, session)
  — klik kartu untuk mulai/pindah live tanpa scroll ke daftar lain;
- strip bisa digeser dengan tombol **‹/›** atau scroll horizontal; kartu device
  yang sedang live diberi highlight amber dan otomatis di-center;
- tombol **Stop** menghentikan stream, tetapi panel live dan strip device tetap
  tersedia untuk memilih HP lain;
- pilihan ukuran live screen: **Fit / Kecil / Sedang / Besar / 1:1 asli** —
  rasio device selalu terjaga, mode **1:1** menampilkan resolusi asli dan bisa
  discroll; resolusi screenshot ditampilkan di header;
- panel live **interaktif** (tap, swipe, ketik, Back/Home/Enter, dan
  **Mute/Unmute** suara HP);
- tombol **Connect all** / **Disconnect all**;
- **mode gelap/terang** lewat tombol matahari/bulan di topbar; pilihan disimpan
  di browser dan default mengikuti `prefers-color-scheme` sistem;
- **layout dua kolom**: live screen di kiri dan panel **Control** di kanan berisi
  accordion **Setup rack / Run action / Campaign / Automation / Jobs / Activity**
  (hanya satu panel terbuka pada satu waktu);
- **Setup rack di UI**: klik **Scan ADB**, edit slot/UDID/port, tambah/hapus
  device, lalu **Simpan & terapkan** — user tidak perlu mengedit JSON. Dashboard
  bisa start tanpa manifest dan membuatnya dari UI;
- **Scheduler**: setiap action/bulk/campaign bisa diberi waktu eksekusi
  (`datetime-local`); job berstatus `scheduled`, tersimpan di
  `<manifest>.state.json`, dan tetap ada setelah dashboard restart. File state
  ini di-ignore Git dan juga menyimpan daily limit/analytics serta status mute
  suara per device;
- **Run action**: pilih **platform** (Facebook, Instagram, Threads, X,
  TikTok); aksi follow/post/comment/**like/save/reply** muncul sesuai
  capability platform, target semua device atau satu device;
- **Campaign**: kirim satu teks post ke beberapa platform sekaligus dengan jeda
  antar platform, target device, dan jadwal opsional;
- **Automation**: batas harian per device (follow/post/comment/like/save/reply),
  health check Appium, analytics hari ini, dan browser notification;
- **Bulk campaign**: mode Single/Bulk, tempel/import daftar target
  (`.txt`/`.csv`), jeda antar item, retry otomatis, progress bar, cancel, dan
  retry item gagal;
- daftar job terbaru beserta status dan pesan hasil;
- **Activity** log real-time (polling 2 detik, pause/clear);
- banner offline otomatis bila server tidak terjangkau.

Live screen memakai screenshot Appium (`driver.get_screenshot_as_png()`) yang
dikirim sebagai **MJPEG** (`multipart/x-mixed-replace`), jadi browser cukup
menampilkan `<img src="...">` tanpa library tambahan. Satu HP hanya punya **satu
capture loop** untuk panel live, dan loop otomatis berhenti saat tombol **Stop**
ditekan atau device gagal 5× berturut-turut.

Catatan performa: screenshot Appium tidak gratis. Hanya device yang sedang
dipilih yang di-stream; tekan **Stop** saat tidak dipantau, atau naikkan
interval lewat `--stream-interval` (default 1 detik, query stream di-clamp
0.3–10 detik).

### Live screen interaktif

Klik gambar live screen = **tap** di titik tersebut, drag = **swipe** (durasi
mengikuti gerakan), isi kolom teks lalu **Kirim** = mengetik ke field yang
sedang fokus, dan tombol **Back/Home/Enter** mengirim keyevent Android.

Browser mengirim koordinat sebagai fraksi 0–1; server mengalikannya dengan
`driver.get_window_size()` perangkat, sehingga tetap akurat berapa pun ukuran
window browser. Input dieksekusi **langsung** (bukan antrean job):

```http
POST /api/devices/<udid>/input
{"type":"tap","x":0.5,"y":0.5}
{"type":"swipe","from":{"x":0.5,"y":0.8},"to":{"x":0.5,"y":0.2},"duration":300}
{"type":"text","text":"Halo"}
{"type":"key","key":"back"}      # back|home|enter|delete
```

Device offline → `409`; driver/Appium gagal → `502`. Endpoint ini bisa dipakai
untuk debugging manual maupun test otomasi UI.

### Mute suara device di live session

Tombol **Mute** / **Unmute** di panel live membisukan audio media HP tanpa
menghentikan live screen, berguna saat memantau banyak HP sekaligus:

- HP yang terhubung ke komputer dashboard (adb tersedia) diatur lewat
  `adb shell` — volume stream media diset ke `0`, lalu **dikembalikan ke level
  sebelumnya** saat Unmute (`cmd media_session volume`, fallback `media volume`);
- HP remote (mis. lewat GADS/Appium) memakai keycode `VOLUME_MUTE` (164) lewat
  sesi Appium;
- status mute disimpan di `<manifest>.state.json`, jadi label tombol tetap benar
  setelah dashboard restart, dan `/api/status` menyertakan `muted` per device.

```http
POST /api/devices/<udid>/audio
{"muted": true}     # atau {"muted": false}
```

Respons berisi `ok`, `muted`, plus `method` (`adb`/`appium`) dan `volume` bila
tersedia. Device offline → `409`; body tidak valid → `400`; gagal di
driver/adb → `502`.

Aksi tidak dieksekusi di request HTTP. Semua masuk **antrean job** dan diproses
satu per satu oleh worker background, sehingga:

- request `POST` langsung mengembalikan `202 Accepted` + `job.id`;
- UI cukup polling `/api/status` dan `/api/logs`;
- connect 20 HP tetap memakai paralelisme internal `SocialMediaBot`
  (`max_workers`), sedangkan job lain menunggu agar tidak saling mengunci;
- kegagalan per device dilaporkan di `job.result.devices`, bukan mematikan job.

API yang dipakai UI:

| Method | Path | Keterangan |
| --- | --- | --- |
| `GET` | `/api/status` | Snapshot rak, device, summary, 10 job terakhir |
| `GET` | `/api/config` | Konfigurasi rack yang bisa diedit dari UI |
| `GET` | `/api/config/scan` | Scan `adb devices` untuk setup rack |
| `POST` | `/api/config` | Simpan rack + device + port ke manifest dan terapkan |
| `GET` | `/api/logs?since=<id>` | Log baru sejak id tertentu |
| `GET` | `/api/devices/<udid>/stream?interval=1` | MJPEG live screen (dipakai `<img>`) |
| `GET` | `/api/devices/<udid>/screenshot` | Satu snapshot PNG (debug/fallback) |
| `POST` | `/api/devices/<udid>/input` | Tap/swipe/teks/key interaktif dengan koordinat fraksi 0–1 |
| `POST` | `/api/devices/<udid>/audio` | `{"muted": true\|false}` — mute/unmute suara media HP untuk live session |
| `POST` | `/api/connect` | Body `{}` atau `{"udids": [...]}`; antrekan connect |
| `POST` | `/api/disconnect` | Antrekan disconnect all |
| `POST` | `/api/action` | `{"platform": "...", "action": "follow\|post\|comment\|like\|save\|reply", "target": "all\|<udid>", "params": {...}, "schedule_at": "ISO-8601 optional"}` |
| `POST` | `/api/bulk` | `{"platform": ..., "action": ..., "target": ..., "items": ["..."], "interval": 0, "retries": 0, "schedule_at": "optional"}` |
| `POST` | `/api/campaign` | `{"text": "...", "platforms": ["facebook", "x"], "target": "all", "interval": 5, "schedule_at": "optional"}` |
| `POST` | `/api/health` | Health check Appium untuk device ter-connect |
| `GET` | `/api/automation` | Limits, usage, dan analytics hari ini |
| `POST` | `/api/automation` | Body `{"limits": {"follow": 30, ...}}` |
| `POST` | `/api/jobs/<id>/cancel` | Batalkan bulk/campaign/scheduled job |
| `POST` | `/api/jobs/<id>/retry` | Ulangi job gagal / item bulk yang gagal total |

Contoh body `POST /api/config`:

```json
{
  "rack": "rack-01",
  "host": "127.0.0.1",
  "devices": [
    {"udid": "R58M00000001", "appium_port": 4723, "device_name": "slot-01"}
  ]
}
```

Konfigurasi hanya bisa disimpan saat tidak ada device ter-connect dan tidak ada
job queued/running.

Contoh:

```bash
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool

curl -s -X POST http://127.0.0.1:8080/api/connect \
  -H 'Content-Type: application/json' -d '{}'

curl -s -X POST http://127.0.0.1:8080/api/action \
  -H 'Content-Type: application/json' \
  -d '{"platform":"instagram","action":"follow","target":"all","params":{"username":"target.account"}}'

curl -s -X POST http://127.0.0.1:8080/api/bulk \
  -H 'Content-Type: application/json' \
  -d '{"platform":"instagram","action":"follow","target":"all","items":["user.one","user.two"],"interval":5,"retries":1}'
```

Windows PowerShell (tersedia di Windows 10+; gunakan `curl.exe` bila ingin
memakai contoh curl di atas, karena `curl` di PowerShell adalah alias):

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/status | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/connect `
  -ContentType "application/json" -Body "{}"

$body = @{
  platform = "instagram"
  action = "follow"
  target = "all"
  params = @{ username = "target.account" }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/action `
  -ContentType "application/json" -Body $body
```

### Bulk campaign

Aktifkan mode **Bulk** di panel Run action, lalu isi daftar target:

- **Follow**: satu username per baris.
- **Create post**: satu teks post per baris.
- **Comment**: `https://post-url | komentar` per baris.

File `.txt`/`.csv` bisa diimpor lewat file picker (satu item per baris).
Batas: **200 item**, `interval` 0–300 detik, `retries` 0–5.

Job bulk berjalan item demi item pada target device yang dipilih. Panel **Jobs**
menampilkan progress bar (`done/total`, ok, gagal, item saat ini). Tombol
**Cancel** menghentikan job di antara item; **Retry** hanya mengulang item yang
gagal total, sehingga device yang sudah sukses tidak diulang (menghindari
follow/comment ganda).

File utama: `dashboard.py` (state/job queue/HTTP API/CLI) dan `dashboard_ui/`
(`index.html`, `styles.css`, `app.js`).

## API

| Method / property | Keterangan |
| --- | --- |
| `SocialMediaBot(host, *, scheme, client_secret, platform_name, automation_name, implicit_wait, new_command_timeout, max_workers=8)` | Konfigurasi global untuk satu rak. |
| `connect_device(udid, appium_port, **opsi)` | Membuat satu session Appium. UDID yang sama akan menutup session lama lebih dulu. |
| `connect_devices(devices, *, continue_on_error=False, max_workers=None)` | Menerima `Rack`, `{udid: port}`, iterable `(udid, port)`, atau iterable `DeviceSpec`. Paralel dan mengembalikan `udid -> driver` sesuai urutan input. |
| `run_on_devices(action, udids=None, *, max_workers=None, raise_on_error=False)` | Menjalankan aksi massal; hasil `udid -> DeviceTaskResult` (`ok`, `result`, `error`). |
| `default_platforms()`, `platform_catalog()` | Registry 5 adapter platform + capability matrix untuk dashboard/API. |
| `platforms["instagram"].follow_user(driver, username, *, delay_range=(3, 7), timeout=20)` | Flow Instagram: buka app, cari akun, buka profil, tekan Follow. |
| `platforms["threads"].create_post(driver, text, ...)` | Flow Threads: composer, ketik teks, tekan Post. |
| `platforms["x"].comment_on_post(driver, url, text, ...)` | Buka URL status X, buka reply, ketik, kirim. |
| `platforms["tiktok"].follow_user(...)` / `.comment_on_post(...)` | Follow & komentar TikTok (posting video belum didukung). |
| `follow_user(driver, username, *, delay_range=(3, 7), timeout=20, package=...)` | Flow Facebook: buka app, cari username, buka profil, tekan Follow; jeda acak 3–7 detik antar langkah. Mengembalikan `FollowResult`. |
| `create_post(driver, post_text, *, delay_range=(3, 7), timeout=20, package=...)` | Flow Facebook: buka app, tap composer "What's on your mind", ketik `post_text`, tekan Post; jeda acak 3–7 detik. Mengembalikan `PostResult`. |
| `comment_on_post(driver, target_url, comment_text, *, delay_range=(3, 7), timeout=20, package=...)` | Buka URL/deep link postingan di app Facebook, buka kotak komentar, ketik dan kirim komentar; jeda acak 3–7 detik. Mengembalikan `CommentResult`. |
| `reconnect_device(udid, *, attempts=3, delay=2.0)` | Menutup driver lama dan membuat session baru dengan retry. |
| `disconnect_device(udid, *, ignore_missing=False)` | Menutup satu session. |
| `disconnect_all()` | Menutup semua session; kegagalan satu HP hanya di-log. Mengembalikan jumlah session yang ditutup. Idempotent. |
| `get_driver(udid)`, `get_connection(udid)` | Driver/booking koneksi; raise `DeviceNotConnectedError` bila belum terhubung. |
| `drivers`, `connections`, `device_ids` | Snapshot registry. Setelah batch connect, urutannya mengikuti urutan rak. |
| `len(bot)`, `udid in bot`, `for udid in bot` | Akses cepat ke registry. |

`DeviceSpec(udid, appium_port, connect_options)` dan `Rack(name, devices, host)`
membantu menyusun konfigurasi 20 HP secara eksplisit.

## Tuning untuk 20 HP

- **Worker**: mulai dari 8. Naikkan ke 10–12 bila host Appium masih longgar;
  turunkan bila CPU/RAM host tinggi atau banyak HP gagal timeout.
- **RAM/CPU**: tiap proses Appium (Node) biasanya makan ~150–300 MB. Untuk 20 HP,
  siapkan minimal 6–8 GB RAM khusus Appium + Android platform tools, di luar
  aplikasi media sosial yang berjalan di HP.
- **Startup bertahap**: saat menyalakan rak, jalankan Appium server bergiliran
  (mis. 4–5 proses per batch) agar `adb` dan USB controller tidak kewalahan.
- **Port**: pastikan tidak ada proses lain di `4723..4742`
  (`lsof -iTCP:4723-4742 -sTCP:LISTEN`).
- **Timeout**: `new_command_timeout` default 300 detik. Untuk sesi farm yang lama,
  naikkan (mis. 600) atau kirim command periodik agar session tidak expired.
- **`no_reset=True`** (default) mempertahankan login akun media sosial antar-run.
  Jangan set `fullReset` di farm produksi karena akan menghapus data akun.
- **Log**: satu logger untuk 20 HP bisa ramai. Gunakan
  `SocialMediaBot(logger=logging.getLogger("rack-01"))` dan format yang memuat
  nama device (log connect/disconnect sudah menyertakan UDID).

## Mode cepat (tuning performa)

Aplikasi ini selalu lebih lambat daripada kontrol manual di GADS — itu memang
sifat Appium: setiap aksi adalah beberapa round-trip UiAutomator2, sementara
GADS hanya streaming video + injeksi tap. Kalau terasa terlalu lambat, ada tiga
tuas berikut (semuanya opsional, default tetap aman).

> Detail mekanisme, alur frame, prioritas nilai, batasan, dan troubleshooting:
> **[PERFORMANCE.md](PERFORMANCE.md)**.

### 1. Live screen: `adb` alih-alih Appium (default `auto`)

```bash
python3 dashboard.py --manifest rack-01.json --screen-source auto --stream-interval 0.3
```

- `auto` (default) memakai `adb -s <udid> exec-out screencap -p` untuk HP/emulator
  yang menempel di host dashboard, lalu **jatuh otomatis ke Appium** untuk device
  remote (mis. device GADS). Tidak ada konfigurasi tambahan.
- Keuntungannya bukan hanya fps lebih tinggi: screenshot Appium memakai server
  UiAutomator2 yang sama dengan aksi, jadi polling live screen ikut memperlambat
  posting/comment. Jalur adb sepenuhnya di luar itu.
- `--screen-source appium` memaksa perilaku lama (semua frame lewat Appium).
- `--stream-interval` minimum `0.3` detik. Untuk local device, `0.2–0.3` sudah
  cukup halus.

### 2. Capability performa: `--fast-mode`

```bash
python3 dashboard.py --manifest rack-01.json --fast-mode
```

Setara dengan `SocialMediaBot(fast_mode=True)`:

- capability `appium:disableWindowAnimation` (animasi window dimatikan), dan
- setting UiAutomator2 `waitForIdleTimeout=0` + `waitForSelectorTimeout=1000`
  yang di-push setelah sesi dibuat.

`waitForIdleTimeout` default-nya 10 detik dan ditunggu **sebelum setiap
command**; di feed yang selalu beranimasi (Facebook/Instagram) ini penyebab
utama aksi terasa menggantung. Risikonya: tap bisa terjadi sedikit lebih awal
dari animasi selesai — uji dulu di satu HP sebelum seluruh rak.

### 3. Jeda antar langkah: `--action-delay-min/--action-delay-max`

```bash
# default platform (3-7 detik per langkah, post = 4 langkah = 12-28 detik diam)
python3 dashboard.py --manifest rack-01.json

# lebih cepat, masih ada jeda manusiawi
python3 dashboard.py --manifest rack-01.json \
  --action-delay-min 1 --action-delay-max 2.5
```

Nilai ini diteruskan sebagai `delay_range` ke setiap aksi (follow/post/comment/
like/save/reply), baik job tunggal, bulk, maupun campaign. Bisa juga diatur dari
kode: `SocialMediaBot(action_delay_range=(1.0, 2.5))`.

> Jeda 3–7 detik itu **disengaja untuk anti-ban**. Menurunkannya menaikkan
> risiko checkpoint platform. Turunkan bertahap dan pantau akun.

Nilai tuning yang aktif terlihat di `GET /api/status` pada blok `tuning`
(`fast_mode`, `action_delay_range`, `screen_source`).

### Rekomendasi hybrid dengan GADS

Untuk hasil paling enak dilihat: **pakai GADS untuk live view/manual control**
(MJPEG/WebRTC di sisi device, jauh lebih halus), sementara SocialMediaBot tetap
menjalankan aksi terjadwal + live screen seperlunya. Kalau ingin semuanya di
dashboard, kombinasi `--screen-source auto --stream-interval 0.3 --fast-mode`
memberi perbaikan paling terasa tanpa menyentuh GADS.

## Catatan GADS

**GADS bersifat opsional.** Untuk rack lokal dengan kabel USB + Appium per port
seperti panduan ini, Anda tidak perlu GADS, client secret, atau `server_path`.
Kode ini kompatibel dengan GADS bila ingin memakai hub/provider remote:

- **Endpoint Appium per device** (port berbeda per HP): isi
  `server_path="device/<device-id>/appium"` dan `client_secret="..."` pada
  `SocialMediaBot(...)`; capability `gads:clientSecret` otomatis dikirim.
- **Appium grid** (`http://<hub>:10000/grid`): gunakan satu host + `server_path`
  yang sama, lalu targetkan HP lewat `extra_capabilities={"appium:udid": ...}`.
  Grid memilih device bebas; `gads:queueTimeout` bisa ditambahkan lewat
  `extra_capabilities` bila ingin menunggu device tersedia.
- Semua `gads:*` capability di-strip sebelum request diteruskan ke Appium,
  sehingga secret tidak muncul di log Appium.

## Capability yang dikirim

```python
{
    "platformName": "Android",
    "appium:automationName": "UiAutomator2",
    "appium:udid": "<udid>",
    "appium:deviceName": "<udid atau device_name dari manifest>",
    "appium:noReset": True,
    "appium:newCommandTimeout": 300,
    # opsional:
    "appium:platformVersion": "14",
    "appium:appPackage": "com.instagram.android",
    "appium:appActivity": ".activity.MainTabActivity",
    # hanya bila client_secret diisi (GADS):
    "gads:clientSecret": "***",
}
```

Session dibuat dengan API modern `UiAutomator2Options` +
`webdriver.Remote(..., options=...)` (W3C).

## Catatan operasional

- Registry dilindungi `threading.RLock`; connect/reconnect/disconnect untuk UDID
  yang sama diserialisasi lewat lock per device, jadi aman untuk 20 worker.
- Selalu panggil `disconnect_all()` di `finally` agar tidak ada session
  menggantung di 20 Appium server.
- Satu instance `SocialMediaBot` per rak/proses. Untuk beberapa rak, buat
  instance terpisah per host Appium.
- Patuhi ketentuan layanan platform media sosial yang Anda otomatiskan.

## Diagnosa koneksi (`diagnose.py`)

Kalau `Connect all` menghasilkan `0/3 device(s) connected`, jalankan diagnosa
untuk melihat penyebabnya per HP (manifest → adb → Appium per port):

```bash
cd tools/social_media_bot
python3 diagnose.py --manifest rack-01.json
python3 diagnose.py --manifest rack-01.json --live-test   # coba session Appium sungguhan
python3 diagnose.py --manifest rack-01.json --no-adb --appium-host 192.168.1.20
```

Script mencetak status tiap slot (`OK`/`MASALAH`) beserta alasannya, misalnya
`tidak ada Appium di http://127.0.0.1:4724/status (Connection refused)` atau
`UDID 'R58M00000001' tidak ada di adb devices`, lalu menutup dengan saran
perbaikan. Exit code `0` = semua siap, `1` = ada masalah, `2` = manifest tidak
terbaca.

Baris pertama output adalah cek dependency, mis.
`Python 3.12.4: Appium-Python-Client 6.1.7, selenium 4.53.0 [OK]`. Jika tertulis
`TIDAK TERPASANG`/`[MASALAH]`, jalankan install memakai interpreter yang sama
dengan yang dipakai dashboard (`sys.executable` ikut dicetak di pesan saran).

Baris kedua memeriksa environment Android yang dibutuhkan driver
`uiautomator2`, mis. `ANDROID_HOME C:\Users\Andi\AppData\Local\Android\Sdk ·
ANDROID_SDK_ROOT ... · adb ... [OK]`. Bila `[MASALAH]`, Appium akan menolak
membuat session dengan pesan *Neither ANDROID_HOME nor ANDROID_SDK_ROOT …
was exported* — set variabel tersebut ke folder SDK (bukan folder
`platform-tools`), lalu restart Appium.

- `--input adb-devices.txt` memeriksa output `adb devices -l` yang disimpan dari
  PC rack (berguna saat dashboard dijalankan di PC lain).
- `--no-adb` melewati cek UDID.
- `--live-test` benar-benar membuat session Appium per HP dan mencetak error
  aslinya, sama seperti yang dilakukan dashboard. Error umum (Appium belum
  jalan, UDID salah, adb tidak ditemukan, driver uiautomator2 gagal) otomatis
  diberi saran perbaikan.

Pesan job connect di dashboard kini juga menyertakan alasan per device, contoh
`0/3 device(s) connected; failed: R58M0001 (Could not create an Appium session
at http://127.0.0.1:4723: ...)`, dan `result.errors` memuat pesan lengkapnya
lewat `GET /api/jobs/<id>`.

## Dump UI (`dump_ui.py`)

Kalau tampilan aplikasi berubah (mis. halaman **Buat postingan baru** di
Facebook) dan aksi gagal karena elemen tidak ditemukan, buka layar tersebut di
HP lalu dump hierarkinya:

```bash
python3 dump_ui.py --manifest rack-01.json --slot slot-01
python3 dump_ui.py --manifest rack-01.json --udid R58M00000001 --out fb-post.xml
```

Output mencantumkan `class`, `text`, `content-desc`, `resource-id`, dan `bounds`
untuk setiap elemen berlabel, sehingga locator di `platforms/*.py` bisa
disesuaikan. File XML penuh (`--out`) bisa dikirim untuk analisis.

## Menjalankan test

macOS / Linux (dari root repo):

```bash
python3 -m unittest discover -s tools/social_media_bot/tests -v
```

Windows PowerShell (dari root repo, atau `cd tools\social_media_bot` lalu
`python -m unittest discover -s tests -v`):

```powershell
python -m unittest discover -s tools\social_media_bot\tests -v
```

Test memakai `unittest` dan mem-mock `webdriver.Remote`, sehingga tidak butuh HP
sungguhan. Cakupan: koneksi 20 HP paralel, isolasi kegagalan, lock per UDID,
aksi massal, reconnect, parsing manifest (termasuk UTF-8 BOM), alur `follow_user`
(happy path, `already_following`, tombol Follow tidak ditemukan, validasi
argumen, delay acak 3–7 detik), alur `create_post` (happy path, label Indonesia,
multiline, tombol Post hilang, validasi teks, delay acak, bounds pill/editor
identik, layar composer sudah terbuka, tap composer diulang, label tombol
terbungkus + tap koordinat, editor berbasis `View`, auto UI dump saat gagal,
reuse layar composer sisa run, deep link `fb://feed` saat pill hilang, scroll
feed ke atas, dismiss popup, wake screen, retry sekali sebelum submit, tidak
retry setelah tombol Post, konteks device di pesan error), alur
`comment_on_post` (deep link, kotak komentar tersembunyi, label Indonesia,
fallback IME/Enter, URL gagal dibuka, foreground gagal, validasi, delay acak),
registry & capability 5 platform (Instagram, Threads, X, TikTok), flow
follow/post/comment/like/save/reply lintas platform, penolakan post media-only,
dashboard (job connect/disconnect/action, bulk campaign follow/comment +
progress, retry item gagal, cancel, platform dispatch, **scheduler persisten,
campaign lintas platform, daily limit, analytics, health check**, status/log
API, static assets, validasi request, log capture, live stream MJPEG, snapshot
PNG, share capture per device, interactive input tap/swipe/text/key dengan
scaling koordinat, **mute/unmute suara via adb dan fallback keycode Appium +
persistensi state**, auto-stop stream), encoding console Windows, pembuatan
manifest dari output adb, serta round-trip capability lewat
`UiAutomator2Options` asli bila `Appium-Python-Client` terpasang.

## Troubleshooting Windows

- **`python` / `py` tidak dikenali** — install Python 3.9+ dari python.org dan
  centang **Add python.exe to PATH**, lalu buka terminal baru.
- **`Activate.ps1 cannot be loaded`** — jalankan
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, atau gunakan
  `.venv\Scripts\activate.bat` dari Command Prompt.
- **`adb` tidak dikenali** — tambahkan folder Android `platform-tools` ke
  `PATH`, lalu buka terminal baru.
- **`Neither ANDROID_HOME nor ANDROID_SDK_ROOT … was exported`** — `adb` di
  `PATH` saja tidak cukup. Set `ANDROID_HOME` **dan** `ANDROID_SDK_ROOT` ke
  folder yang berisi `platform-tools`:
  - Android Studio: `%LOCALAPPDATA%\Android\Sdk`
  - platform-tools dari winget: folder induknya, mis.
    `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe`

  Driver `uiautomator2` juga butuh JDK 17+: set `JAVA_HOME` ke folder JDK
  (mis. `C:\Program Files\Microsoft\jdk-17...`). Tutup dan buka ulang window
  Appium setelah mengubah variabel; cek hasilnya dengan `diagnose.py`.
- **`appium` tidak dikenali** — install Node.js LTS, jalankan
  `npm install -g appium`, lalu buka terminal baru.
- **`appium.ps1 cannot be loaded because running scripts is disabled`** —
  kebijakan eksekusi PowerShell memblokir shim npm, bukan masalah instalasi.
  Pilih salah satu:
  - jalankan `start-appium.bat` (satu window cmd per port, policy tidak dipakai);
  - per sesi: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`;
  - permanen untuk user: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`;
  - tanpa mengubah policy: `appium.cmd -p 4723`, `cmd /c appium -p 4723`, atau
    `cmd /c appium driver install uiautomator2`.
- **Port Appium sudah dipakai** — cek dengan
  `netstat -ano | findstr :4723`; hentikan proses lama atau ganti port di
  manifest.
- **`.venv` disalin dari macOS/Linux** — hapus folder `.venv`, lalu buat ulang
  dengan `py -3 -m venv .venv`. Venv Windows memakai `Scripts\`, bukan `bin/`.
