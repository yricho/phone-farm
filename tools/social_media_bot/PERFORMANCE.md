# PERFORMANCE — cara kerja live screen & tuning aksi

Dokumen ini menjelaskan **mekanisme** di balik tiga tuas performa
`tools/social_media_bot`:

1. sumber frame live screen (`adb` vs Appium),
2. tuning jeda aksi (`delay_range`), dan
3. `fast_mode` (capability + setting UiAutomator2).

Panduan pemakaian singkat ada di [README](README.md#mode-cepat-tuning-performa);
dokumen ini fokus pada *bagaimana* semuanya bekerja, nilai default, dan batasannya.

> Ringkas: default aplikasi **tidak berubah**. Semua tuas bersifat *opt-in*
> (flag CLI atau argumen `SocialMediaBot(...)`), jadi perilaku lama tetap
> tersedia dan aman untuk farm produksi.

## 1. Ringkasan tuas

| Tuas | Aktif lewat | Default | Efek utama |
| --- | --- | --- | --- |
| Sumber frame live screen | `--screen-source auto\|appium` | `auto` | Frame lokal lewat `adb exec-out screencap` (raw/PNG dipilih otomatis), remote lewat `--mjpeg-url` bila diisi, sisanya Appium |
| Interval live screen | `--stream-interval` (atau `?interval=` di URL) | otomatis: `0.3` dtk (adb/MJPEG), `1.0` dtk (Appium); clamp `0.3–10` | Semakin kecil semakin halus |
| Ukuran/kualitas frame | `--stream-max-width`, `--stream-quality` | `720` px, JPEG `70` (butuh Pillow) | Frame ~80 KB, bukan PNG 0,5–3 MB |
| Stream upstream untuk device remote | `--mjpeg-url`, `--mjpeg-token` | mati | Device GADS memakai MJPEG provider, bukan screenshot Appium |
| Jeda antar langkah aksi | `--action-delay-min/--action-delay-max` atau `SocialMediaBot(action_delay_range=...)` | platform: `(3.0, 7.0)` | Waktu diam di dalam alur follow/post/comment |
| Mode cepat UiAutomator2 | `--fast-mode` / `fast_mode=True` | mati | `waitForIdleTimeout=0`, `waitForSelectorTimeout=1000`, animasi window dimatikan |

Nilai yang sedang aktif selalu bisa dilihat di `GET /api/status` pada blok
`tuning` (§6).

## 2. Prinsip desain

- **Default aman.** Tanpa flag, perilaku otomasi tidak berubah: jeda tetap
  3–7 detik, idle wait UiAutomator2 tetap standar. Live screen memakai jalur
  tercepat yang tersedia dan tetap jatuh ke Appium bila perlu.
- **Fallback selalu tersedia.** Jalur cepat (adb) tidak pernah menjadi syarat;
  kalau tidak memungkinkan, sistem kembali ke Appium tanpa error ke pengguna.
- **Satu sumber kebenaran untuk tuning.** Nilai tuning dioper dari CLI →
  `SocialMediaBot`/`DashboardState` → dipakai di titik eksekusi, bukan disebar
  ke UI/manifest.
- **Tidak menyentuh data akun.** Tidak ada `fullReset`/`clear`; `no_reset=True`
  tetap default sehingga login di HP dipertahankan.

## 3. Cara kerja live screen

### 3.1 Alur satu frame

```text
Browser (MJPEG <img>)
   │  GET /api/devices/<udid>/stream          (tanpa ?interval → server memilih)
   ▼
DashboardRequestHandler._serve_stream()
   │  ScreenStreamManager.acquire(udid, interval)
   │    ├─ pastikan device punya sesi Appium (get_driver → raise kalau offline)
   │    └─ buat/pakai ulang DeviceStream (satu per UDID, dibagi semua viewer)
   ▼
DeviceStream._capture_loop()  (thread daemon, satu per UDID)
   │  1. sumber frame berurutan, yang pertama berhasil dipakai:
   │       a. "adb"   → AdbFrameGrabber.capture()   (device menempel di host)
   │       b. "mjpeg" → MjpegFrameSource.capture()  (bila --mjpeg-url diisi)
   │       c. "appium"→ driver.get_screenshot_as_png()
   │  2. FrameEncoder: kecilkan ke --stream-max-width, encode JPEG (Pillow)
   │  3. simpan frame + notify subscriber
   └─ sleep(interval - elapsed)   (MJPEG: polling 50 ms, duplikat dilewati)
   ▼
multipart/x-mixed-replace, tiap part: Content-Type: image/jpeg (atau image/png)
```

Poin penting:

- **Satu loop per device, bukan per viewer.** Dua tab browser yang menonton HP
  yang sama berbagi frame yang sama, dan frame di-encode sekali saja.
- **Interval.** UI tidak lagi mengirim `?interval` (dulu selalu `1`, sehingga
  `--stream-interval` tidak pernah berlaku). Urutan prioritas: viewer tercepat
  yang mengirim `?interval=` → `--stream-interval` → otomatis (`0.3` dtk untuk
  frame adb/MJPEG, `1.0` dtk untuk screenshot Appium agar tidak berebut dengan
  aksi). Semua nilai di-clamp `0.3–10`.
- **Stream tetap butuh sesi Appium.** Validasi `get_driver(udid)` di `acquire()`
  membuat device yang belum di-*Connect* tidak bisa dibuka live screen-nya.
- **Stream hidup selama ada subscriber.** `remove_subscriber()` menutup stream
  saat subscriber terakhir pergi; `close_all()` dipakai saat Disconnect.
- **Gagal 5× berturut-turut → stream ditutup.** Sumber yang melempar exception
  dilewati (lanjut ke sumber berikutnya); yang dihitung adalah kegagalan Appium
  sebagai pilihan terakhir.
- **Ganti host Appium ditolak saat ada stream aktif**
  (`ScreenStreamManager.set_bot` → `RuntimeError`).

### 3.2 `FrameEncoder` — kenapa JPEG kecil

Panel live hanya selebar ±280–400 px CSS, sedangkan screenshot HP 1080×2400.
Mengirim PNG penuh (0,5–3 MB) setiap frame membuang bandwidth dan membuat
browser men-decode PNG besar berkali-kali per detik. `FrameEncoder`
mengecilkan frame ke `--stream-max-width` (default `720`) lalu meng-encode JPEG
kualitas `--stream-quality` (default `70`):

| Input (1080×2400) | Output | Biaya di host |
| --- | --- | --- |
| PNG dari `screencap -p` (~0,5 MB) | JPEG ~80 KB | ~35 ms |
| Raw dari `screencap` (~10 MB) | JPEG ~80 KB | ~25 ms |

- **Pillow opsional.** Tanpa Pillow frame dikirim apa adanya sebagai PNG
  (perilaku lama) dan dashboard mencetak catatan saat start. Pasang dengan
  `pip install -r requirements.txt`.
- Frame JPEG dari upstream MJPEG diteruskan tanpa encode ulang.
- PNG yang tidak bisa dibaca tetap ditampilkan (pass-through), bukan dibuang.
- Tombol **Snapshot** mengambil PNG resolusi penuh lewat
  `GET /api/devices/<udid>/screenshot`, bukan dari frame JPEG yang diperkecil.

### 3.3 `AdbFrameGrabber`

Dibuat sekali per device (dipakai ulang oleh stream dan snapshot).

| Tahap | Perintah | Perilaku |
| --- | --- | --- |
| Probe (lazy) | `adb -s <udid> get-state` | `device` + exit 0 → jalur adb dipakai; selain itu → sumber berikutnya. Probe gagal diulang tiap **30 dtk** |
| Benchmark (sekali) | `screencap` lalu `screencap -p` | Bila Pillow ada: satu frame raw dan satu frame PNG diukur, format yang lebih cepat dipakai seterusnya |
| Ambil frame | `adb -s <udid> exec-out screencap [-p]` | Timeout 5 dtk; PNG harus diawali magic PNG, raw harus punya header 12/16 byte yang cocok dengan ukuran data |
| Kegagalan | — | Setelah **3** kegagalan beruntun jalur adb dijeda **30 dtk** (`ADB_RETRY_SECONDS`), lalu dicoba lagi |

Kenapa dua format: `screencap -p` membuat **HP** mengompres PNG layar penuh
(ratusan ms CPU HP), sedangkan `screencap` tanpa `-p` mengirim piksel mentah
(~4 byte/piksel, tanpa encode). Di emulator dan USB 3 raw biasanya menang; di
USB 2 yang padat PNG bisa lebih cepat. Benchmark memilih otomatis per device;
log `Live screen <udid> uses adb raw|png frames (...)` menunjukkan hasilnya.
Format raw yang tidak dikenal (pixel format selain RGBA/RGBX/BGRA) otomatis
membuat device itu memakai PNG.

Konsekuensi yang perlu diketahui:

- Probe berjalan di **thread stream**, bukan di request HTTP, sehingga membuka
  live screen tidak pernah menggantung menunggu adb.
- Device remote (mis. device GADS `emu_<provider>_<AVD>`) gagal di probe →
  sumber berikutnya (MJPEG bila dikonfigurasi, lalu Appium).
- Bila `adb` tidak ada di `PATH`, `FileNotFoundError` ditangkap → dianggap
  "tidak tersedia". Pakai `--adb /path/ke/adb` bila perlu.
- Satu frame tetap satu proses `adb`. Proses `adb` yang dipertahankan antar
  frame sengaja tidak dipakai: memisahkan frame dari aliran byte tidak bisa
  diandalkan lintas versi Android, sementara biaya spawn (~30–80 ms di Windows)
  kecil dibanding biaya capture itu sendiri.

### 3.4 `MjpegFrameSource` — device remote / GADS

Device yang tidak menempel di host dashboard sebelumnya selalu lewat screenshot
Appium (UiAutomator2), yang antre dengan `click`/`find_element`. Bila provider
GADS menjalankan device tersebut, frame bisa diambil dari stream MJPEG GADS
(dihasilkan aplikasi capture di HP, sama sekali tidak menyentuh UiAutomator2):

```bash
# langsung ke provider GADS (rute provider tidak memakai auth hub)
python dashboard.py --mjpeg-url "http://<provider>:<port>/device/{udid}/android-stream-mjpeg"

# lewat hub GADS yang memakai auth
set GADS_MJPEG_TOKEN=<bearer token hub>
python dashboard.py --mjpeg-url "http://<hub>:10000/device/{udid}/android-stream-mjpeg"
```

- `{udid}` wajib ada dan diganti UDID (di-URL-encode). Bisa juga lewat env
  `GADS_MJPEG_URL`.
- `--mjpeg-token` / `GADS_MJPEG_TOKEN` dikirim sebagai
  `Authorization: Bearer ...` dari server dashboard; token tidak pernah sampai
  ke browser.
- Urutan sumber tetap adb → MJPEG → Appium, jadi HP lokal tetap memakai adb.
- Reader berjalan di thread sendiri, reconnect dengan back-off 1→10 dtk, dan
  memotong frame dengan marker JPEG (`FFD8`…`FFD9`), sehingga upstream tanpa
  `Content-Length` (seperti GADS) tetap terbaca.
- Frame yang lebih tua dari 5 dtk dianggap basi → jatuh ke Appium sampai
  upstream pulih.
- `--screen-source appium` mematikan adb **dan** MJPEG.

### 3.5 Kenapa adb/MJPEG lebih cepat daripada Appium

Screenshot Appium (`get_screenshot_as_png`) dieksekusi oleh **server
UiAutomator2 di device**, memakai jalur yang sama dengan `find_element`,
`click`, dan `send_keys`. Artinya frame berikutnya menunggu command sebelumnya
selesai, dan polling live screen **bersaing** dengan aksi otomasi. `adb exec-out
screencap` dan stream MJPEG berjalan di jalur terpisah.

### 3.6 Batas yang tetap berlaku

- `--stream-interval`/`?interval=` di bawah `0.3` tetap di-clamp ke `0.3`
  untuk adb dan Appium (MJPEG dipoll 50 ms, mengikuti kecepatan upstream).
- Mode ukuran **Actual** di panel menampilkan frame hasil downscale
  (default lebar 720). Pakai `--stream-max-width 0` untuk resolusi penuh.
- Satu panel live = satu stream; device lain tidak di-capture.

## 4. Cara kerja tuning jeda aksi (`delay_range`)

### 4.1 Empat tingkat prioritas

Nilai `delay_range` yang dipakai sebuah aksi ditentukan berurutan:

```text
1. delay_range eksplisit pada pemanggilan aksi        (menang, tidak ditimpa)
2. DashboardState.action_delay_range                  (flag CLI dashboard)
3. SocialMediaBot.action_delay_range                  (constructor / embedder)
4. Default platform: DEFAULT_DELAY_RANGE = (3.0, 7.0) (platforms/base.py)
```

- Di dashboard, langkah 2 dan 3 saling melengkapi: bila
  `DashboardState(action_delay_range=...)` tidak diisi, nilainya diambil dari
  `bot.action_delay_range`, sehingga aplikasi yang memakai
  `SocialMediaBot(action_delay_range=...)` otomatis konsisten di UI.
- API HTTP **tidak** menerima `delay_range` di dalam `params`: `submit_action`
  dan `submit_bulk` memvalidasi field terhadap `ACTION_FIELDS` dan menolak
  field asing. Jadi tuning dilakukan saat start, bukan per-request.
- Manifest rack juga tidak membawa nilai tuning (hanya `rack`, `host`, dan
  `devices`), dan `.state.json` tidak menyimpannya — tuning bersifat runtime.

### 4.2 Titik injeksi di dashboard

`DashboardState._action_params(action, params)`:
- menyalin parameter job (tidak memutasi payload asli),
- menambahkan `delay_range` hanya bila (a) tuning aktif, (b) `action` ada di
  `ACTION_FIELDS`, dan (c) belum ada `delay_range` eksplisit.

Fungsi ini dipakai oleh tiga jalur yang sama-sama berujung ke adapter platform:

| Job | Pemakaian |
| --- | --- |
| `action` (satu device / semua device) | `_do_action` |
| `bulk` (banyak item) | `_do_bulk` → per item |
| `campaign` (post lintas platform) | `_do_campaign` → `_do_action` |

Adapter platform (`FacebookPlatform`, `InstagramPlatform`, …) meneruskan
`delay_range` ke langkah-langkahnya. Adapter legacy `_LegacyPlatform` (dipakai
test/embedder) tetap mengabaikan kwarg tambahan, sehingga injeksi tidak pernah
mengubah kontraknya.

### 4.3 Berapa lama jeda itu dipakai

`pause()` mengambil nilai acak `uniform(min, max)` **setelah** setiap langkah.
Jumlah langkah berbeda antara adapter Facebook (memakai `facebook.py`) dan
engine platform lain (memakai `platforms/base.py`), dan beberapa jeda bersyarat
(mis. tombol *Next* pada composer Facebook 588):

| Aksi | Jumlah jeda (FB / engine) | Default (3–7 s) | Tuned (1–2,5 s) |
| --- | --- | --- | --- |
| `post` | 5–6 / 4 | 15–42 / 12–28 s | 5–15 / 4–10 s |
| `follow` | 7 / 6–7 | 21–49 / 18–49 s | 7–17,5 / 6–17,5 s |
| `comment` | 3–4 / 3–4 | 9–28 s | 3–10 s |
| `like` / `save` | — / 2 | 6–14 s | 2–5 s |
| `reply` | — / 3–4 | 9–28 s | 3–10 s |

Angka itu **belum** termasuk round-trip Appium (pencarian elemen, klik,
`send_keys`). Jeda ini disengaja untuk meniru perilaku manusia; menurunkannya
menaikkan risiko checkpoint platform.

## 5. Cara kerja `fast_mode`

### 5.1 Capability vs setting — bedanya penting

`waitForIdleTimeout` **bukan capability**, melainkan *setting* server
UiAutomator2. Karena itu `fast_mode` melakukan dua hal berbeda:

| Kapan | Apa yang dikirim | Isi |
| --- | --- | --- |
| Sebelum sesi dibuat | capability `appium:disableWindowAnimation` | `True` — driver mematikan skala animasi window lewat `adb settings` |
| Setelah sesi hidup | `driver.update_settings({...})` | `waitForIdleTimeout=0`, `waitForSelectorTimeout=1000` |

`waitForIdleTimeout` default UiAutomator2 adalah **10.000 ms** dan ditunggu
sebelum hampir setiap command; feed yang terus beranimasi (Facebook/Instagram)
membuat wait ini habis terus. Menyetel `0` mematikan wait tersebut.
`waitForSelectorTimeout` (default 10.000 ms) hanya berlaku untuk locator
`-android uiautomator`, dan diturunkan ke 1.000 ms karena kode sudah punya
loop polling sendiri (`wait_for_any`, interval 0,5 s).

### 5.2 Siklus hidup

```text
connect_device() → _build_capabilities()  [sertakan disableWindowAnimation]
                 → _create_driver()       [sesi Appium dibuat]
                 → implicitly_wait()      [bila diatur]
                 → _apply_fast_settings() [update_settings, hanya fast_mode]

reconnect_device() → _create_driver()
                   → implicitly_wait()
                   → _apply_fast_settings()   ← diulang, karena sesi baru
```

- `_apply_fast_settings` **tidak** dipanggil bila `fast_mode` mati — nol
  perubahan perilaku untuk pengguna lama.
- Bila `driver.update_settings` tidak ada (driver lama) atau melempar error,
  kegagalan dicatat sebagai log `debug` dan **sesi tetap dipakai**. Fast mode
  bersifat best-effort, tidak pernah menggagalkan koneksi.
- Nilai yang dipakai bisa dilihat di `SocialMediaBot.FAST_AUTOMATION_SETTINGS`.

### 5.3 Konsekuensi & batasan

- Karena wait idle dimatikan, tap bisa dieksekusi saat animasi transisi belum
  selesai. Pada UI yang berubah cepat ini bisa berarti tap "melenceng".
  Uji per platform/perangkat sebelum dipakai seluruh rak.
- Fast mode tidak memakai `skipDeviceInitialization`,
  `skipServerInstallation`, atau `autoGrantPermissions` — semuanya punya efek
  samping (unlock/permission state) yang tidak boleh berubah diam-diam.
- Fast mode tidak mempercepat jaringan atau render aplikasi; ia hanya
  menghilangkan waktu tunggu yang dilakukan Appium.

## 6. Observability

### 6.1 `GET /api/status` → `tuning`

```json
"tuning": {
  "fast_mode": true,
  "action_delay_range": [1.0, 2.5],
  "screen_source": "auto",
  "stream_interval": null,
  "stream_max_width": 720,
  "stream_quality": 70,
  "stream_jpeg": true,
  "stream_mjpeg": false
}
```

Setiap device di `devices[]` juga punya blok `stream` selama live screen
terbuka (`null` bila tidak):

```json
"stream": {"source": "adb", "fps": 3.2, "interval": 0.3, "viewers": 1,
           "content_type": "image/jpeg", "frame_bytes": 81234}
```

Panel live menampilkannya di baris **Stream** (mis. `adb · 3.2 fps`). Bila
tertulis `Appium (lambat)`, device tidak terlihat oleh adb di host dashboard.

- `action_delay_range` bernilai `null` bila tidak ada tuning (default platform).
- `fast_mode` dibaca dari bot (`getattr(bot, "fast_mode", False)`), jadi
  embedder yang memakai objek bot lain tetap aman.

### 6.2 Log

| Level | Pesan | Arti |
| --- | --- | --- |
| `debug` | `Could not apply fast-mode UiAutomator2 settings` | `update_settings` gagal; sesi tetap jalan tanpa tuning |
| `warning` | `Screenshot failed for <udid>` | Endpoint screenshot tunggal (Appium) gagal |
| — | Stream tertutup diam-diam | 5 kegagalan frame beruntun; buka ulang live screen |

`AdbFrameGrabber` sengaja tidak menulis log per frame (bisa 3–8 baris/detik);
kegagalannya terlihat dari `stream.source` di status. Satu-satunya log `info`
adalah hasil benchmark format (`Live screen <udid> uses adb raw|png frames`).
Kegagalan upstream MJPEG dicatat di level `debug`.

### 6.3 Mengukur sendiri

```bash
# perkiraan fps: hitung part MJPEG dalam 5 detik
curl -sN "http://127.0.0.1:8080/api/devices/<udid>/stream?interval=0.3" \
  | timeout 5 grep -ac "Content-Type: image/" || true

# atau lihat fps/sumber yang dihitung server
curl -s http://127.0.0.1:8080/api/status | python -m json.tool | grep -A6 '"stream"'

# biaya satu frame adb (target < 0,3 dtk untuk HP USB modern)
time adb -s <udid> exec-out screencap -p > /dev/null

# pastikan tuning aktif
curl -s http://127.0.0.1:8080/api/status | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['tuning'])"
```

## 7. Konfigurasi lengkap

### 7.1 Flag `dashboard.py`

| Flag | Tipe/default | Fungsi |
| --- | --- | --- |
| `--fast-mode` | flag, mati | Kirim capability + setting UiAutomator2 (§5) ke setiap sesi yang dibuat dashboard |
| `--action-delay-min` | float, `None` | Batas bawah jeda; wajib berpasangan dengan `--action-delay-max` |
| `--action-delay-max` | float, `None` | Batas atas jeda |
| `--screen-source` | `auto`\|`appium`, `auto` | `auto`: adb untuk device lokal + fallback Appium; `appium`: selalu Appium |
| `--adb` | path, `adb` | Lokasi binary adb untuk live screen |
| `--stream-interval` | float, otomatis | Interval default live screen (clamp `0.3–10`); kosong = `0.3` adb/MJPEG, `1.0` Appium |
| `--stream-max-width` | int, `720` | Lebar frame live screen; `0` = resolusi device (butuh Pillow) |
| `--stream-quality` | int, `70` | Kualitas JPEG `10–95` (butuh Pillow) |
| `--mjpeg-url` | URL dengan `{udid}`, `$GADS_MJPEG_URL` | Stream MJPEG upstream untuk device remote |
| `--mjpeg-token` | string, `$GADS_MJPEG_TOKEN` | Bearer token untuk `--mjpeg-url` |

Validasi: batas jeda harus memenuhi `0 <= min <= max`; hanya salah satu batas
diisi → dashboard menolak start dengan exit code `2`.

### 7.2 API Python

```python
from social_media_bot import SocialMediaBot, load_rack

bot = SocialMediaBot(
    host="127.0.0.1",
    fast_mode=True,                  # capability + setting (§5)
    action_delay_range=(1.0, 2.5),   # default jeda untuk aksi (§4)
)
bot.connect_devices(load_rack("rack-01.json"), max_workers=8)
print(bot.fast_mode, bot.action_delay_range)   # True (1.0, 2.5)
```

`run_rack.py` juga menerima `--fast-mode` (koneksi CLI, tanpa dashboard).

### 7.3 Yang **tidak** berubah

- Isi manifest rack dan format `.state.json` tetap sama.
- Endpoint HTTP tidak bertambah/berubah kontrak; hanya `status` yang kini
  menyertakan blok `tuning`.
- Payload `params` pada `/api/action` dan item pada `/api/bulk` tetap divalidasi
  ketat; tidak ada field tuning baru di sana.

## 8. Kompatibilitas & catatan perangkat

| Skenario | Live screen | Fast mode | Delay tuning |
| --- | --- | --- | --- |
| HP USB di host dashboard | **adb** (3–8 fps), fallback Appium | berlaku | berlaku |
| Emulator di host dashboard | **adb** (`emulator-5554`), fallback Appium | berlaku | berlaku |
| HP fisik via Wi-Fi ADB di host dashboard | **adb**, bila serial ada di `adb devices` | berlaku | berlaku |
| Device GADS remote (hub di mesin lain) | Appium | berlaku (session tetap dibuat di provider) | berlaku |
| Dashboard dipindah dari host Appium | Appium (adb tidak melihat device) | berlaku untuk sesi dashboard | berlaku |

Catatan:

- Windows: `%*` pada `start-dashboard.bat` meneruskan flag, jadi
  `start-dashboard.bat --fast-mode --action-delay-min 1 --action-delay-max 2.5`
  valid. Bila `adb` tidak ada di `PATH`, pakai `--adb "C:\...\adb.exe"`.
- Emulator: `adb exec-out screencap` jauh lebih cepat daripada lewat Appium,
  tetapi aksi otomasi di emulator tetap lebih lambat daripada HP fisik
  (hierarchy dump lebih berat).
- GADS grid (`/grid`) dan endpoint per-device (`device/<id>/appium`) tidak
  terpengaruh; tuning berjalan di sisi klien SocialMediaBot.

## 9. Troubleshooting

| Gejala | Kemungkinan sebab | Tindakan |
| --- | --- | --- |
| Baris **Stream** menulis `Appium (lambat)` | Device tidak terlihat `adb devices` (remote/GADS) | Cek `adb -s <udid> get-state`; untuk device GADS isi `--mjpeg-url` |
| Frame besar / browser berat, `content_type` = `image/png` | Pillow belum terpasang | `pip install -r requirements.txt` lalu restart dashboard |
| Live screen jadi hitam/error setelah beberapa saat | 5 kegagalan frame → stream ditutup | Buka ulang live screen; cek koneksi USB/Appium |
| `--fast-mode` tapi `tuning.fast_mode` tetap `false` | Dashboard dijalankan tanpa flag / bot dibuat manual | Jalankan `dashboard.py --fast-mode`, atau `SocialMediaBot(fast_mode=True)` |
| Aksi tetap lambat walau `fast_mode` aktif | Bottleneck bukan idle wait (jaringan, render app, atau jeda 3–7 s) | Turunkan `--action-delay-*` bertahap; ukur durasi per langkah |
| Tap meleset setelah `fast_mode` | Wait animasi dimatikan | Matikan fast mode untuk device/platform itu, atau naikkan jeda antar langkah |
| `dashboard.py` menolak start: `set both --action-delay-min ...` | Hanya satu batas diisi | Isi keduanya, atau hapus keduanya |
| Log `Could not apply fast-mode ...` | Driver tidak mendukung `update_settings` | Sesi tetap jalan; update Appium/driver `uiautomator2` |

## 10. Test & jaminan regresi

Suite: `python3 -m unittest discover -s tests -v` → **218 test**, semuanya lulus.

Test yang mengunci mekanisme baru:

| Area | Test |
| --- | --- |
| Capability + setting fast mode, aplikasi saat reconnect, toleransi kegagalan, validasi `action_delay_range` | `FastModeTests` (`tests/test_social_media_bot.py`) |
| Probe/capture adb, caching probe, output non-PNG, adb tidak ada | `AdbFrameGrabberTests` (`tests/test_dashboard.py`) |
| Raw vs PNG, benchmark, retry adb, encoder JPEG, MJPEG upstream, interval per viewer, snapshot, status `stream` | `tests/test_live_screen.py` |
| Pemilihan sumber frame, fallback Appium, `screen_source=appium`, validasi nilai | `ScreenSourceTests` |
| Injeksi `delay_range` pada action/bulk, default tetap, validasi | `ActionTuningTests` |
| Flag CLI baru & validasinya | `PerfFlagTests` |

Selain itu, 194 test lama (termasuk smoke test UI dashboard) tetap lulus tanpa
perubahan, sehingga kontrak lama terjaga.

## 11. Rekomendasi hybrid dengan GADS

Live view GADS (MJPEG/WebRTC di sisi device) tetap yang paling halus untuk
*melihat* dan mengontrol manual. Pola yang disarankan:

1. **GADS** untuk memantau/mengontrol device secara manual saat butuh presisi.
2. **SocialMediaBot** untuk aksi terjadwal (follow/post/comment/bulk/campaign),
   dengan `--fast-mode` dan jeda yang sudah dituning.
3. Kalau semua ingin di dashboard: pasang Pillow, jalankan dengan `--fast-mode
   --action-delay-min 1 --action-delay-max 2.5`, dan untuk device GADS remote
   isi `--mjpeg-url` (§3.4) agar live screen memakai stream MJPEG GADS.

## 12. Referensi kode

| Bagian | Lokasi |
| --- | --- |
| Konstanta default jeda | `platforms/base.py` (`DEFAULT_DELAY_RANGE`, `FAST_DELAY_RANGE`) |
| Loop frame + interval | `dashboard.py` (`DeviceStream`) |
| Sumber frame adb | `dashboard.py` (`AdbFrameGrabber`, `RawFrame`, `ADB_MAX_FAILURES`, `ADB_RETRY_SECONDS`) |
| Encode JPEG | `dashboard.py` (`FrameEncoder`, `DEFAULT_STREAM_MAX_WIDTH`, `DEFAULT_STREAM_QUALITY`) |
| Stream upstream | `dashboard.py` (`MjpegFrameSource`) |
| Pemilihan sumber + sharing stream | `dashboard.py` (`ScreenStreamManager`) |
| Injeksi jeda | `dashboard.py` (`DashboardState._action_params`) |
| Blok `tuning` di status | `dashboard.py` (`DashboardState.status`) |
| Flag CLI | `dashboard.py` (`build_parser`) |
| Fast mode bot | `social_media_bot.py` (`FAST_AUTOMATION_SETTINGS`, `_apply_fast_settings`, `_build_capabilities`) |
| CLI koneksi | `run_rack.py` (`--fast-mode`) |

Dokumen terkait: [README](README.md), [RUNBOOK](RUNBOOK.md),
[RACK_SETUP](RACK_SETUP.md), [docs/appium-credentials.md](../../docs/appium-credentials.md).



