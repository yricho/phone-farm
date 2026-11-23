# RUNBOOK — urutan menjalankan phone farm (Windows)

Urutan ini wajib dipatuhi: **environment → Appium → verifikasi → dashboard → Connect all**.
Dashboard tidak membutuhkan `ANDROID_HOME`; **Appium** yang membutuhkannya, jadi Appium
harus dijalankan dari window yang environment-nya sudah benar.

## Sekali saja (setup)

1. Clone repo, buat venv, install dependency:

   ```powershell
   cd C:\FARM\phone-farm\tools\social_media_bot
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Install Appium + driver (Node.js LTS):

   ```powershell
   npm.cmd install -g appium
   appium.cmd driver install uiautomator2
   ```

3. Set environment (permanen untuk user). Contoh platform-tools dari winget;
   kalau memakai Android Studio, pakai `%LOCALAPPDATA%\Android\Sdk`:

   ```powershell
   winget install --id Microsoft.OpenJDK.17 -e     # kalau JDK belum ada
   $sdk = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe"
   $java = (Get-ChildItem "C:\Program Files\Microsoft" -Directory -Filter "jdk-*" |
            Sort-Object Name -Descending | Select-Object -First 1).FullName
   Test-Path "$sdk\platform-tools\adb.exe"         # True
   Test-Path "$java\bin\java.exe"                  # True
   [Environment]::SetEnvironmentVariable("ANDROID_HOME", $sdk, "User")
   [Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $sdk, "User")
   [Environment]::SetEnvironmentVariable("JAVA_HOME", $java, "User")
   ```

   Setelah ini **tutup semua terminal** (atau sign out/in) agar variabel terbaca.

## Setiap kali mau jalan

### 1. Bersihkan proses lama

```powershell
Get-CimInstance Win32_Process -Filter "name='node.exe'" |
  Where-Object { $_.CommandLine -like '*appium*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

### 2. Window PowerShell **baru** → cek environment

```powershell
echo $env:ANDROID_HOME
echo $env:JAVA_HOME
adb devices -l
```

✅ Checkpoint: dua variabel terisi dan 3 HP berstatus `device`.
❌ Kalau kosong: set `$env:ANDROID_HOME`/`$env:JAVA_HOME` di window itu, atau ulangi setup.

### 3. Start Appium (dari window yang sama)

```powershell
cd C:\FARM\phone-farm\tools\social_media_bot
.\start-appium.bat 4723 4724 4725
```

✅ Checkpoint: baris `ANDROID_HOME=...` pada output **tidak kosong**, dan setiap window
Appium menampilkan `Appium REST http interface listener started on 0.0.0.0:472x`.

### 4. Verifikasi Appium + session

```powershell
curl.exe -s http://127.0.0.1:4723/status
py .\diagnose.py --manifest rack-01.json --live-test
```

✅ Checkpoint: `Siap: 3/3 device (live test)` dan tiga UDID `OK`.
❌ `Neither ANDROID_HOME …` = Appium dijalankan sebelum env benar → ulangi langkah 1–3.

### 5. Start dashboard (window terpisah)

```powershell
cd C:\FARM\phone-farm\tools\social_media_bot
.\start-dashboard.bat
```

Buka `http://127.0.0.1:8080`.

Kalau aksi atau live screen terasa lambat, jalankan dengan tuning performa
(penjelasan lengkap: [README](README.md#mode-cepat-tuning-performa)):

```powershell
.\start-dashboard.bat --fast-mode --action-delay-min 1 --action-delay-max 2.5

# atau langsung tanpa .bat (interval live screen otomatis 0.3 dtk untuk adb)
.\.venv\Scripts\python.exe .\dashboard.py --manifest rack-01.json `
  --screen-source auto --fast-mode `
  --action-delay-min 1 --action-delay-max 2.5
```

- `--screen-source auto` (default): live screen lewat `adb screencap` untuk HP
  yang menempel di PC ini, Appium untuk device remote.
- `--fast-mode`: matikan `waitForIdleTimeout` UiAutomator2 + animasi window.
- `--action-delay-min/max`: jeda antar langkah. Default 3–7 detik (anti-ban);
  turunkan bertahap, jangan langsung ke 0.

Tanpa flag di atas, semua nilai default tetap dipakai dan aman. Mekanisme
lengkap (alur frame, prioritas nilai, batasan): [PERFORMANCE.md](PERFORMANCE.md).

### 6. Di dashboard

1. **Setup rack** → pastikan 3 slot & UDID benar → **Simpan & terapkan**.
2. **Connect all** → job harus `3/3 device(s) connected`.
3. Klik satu device → **live screen** untuk tes.
4. Operasi selesai: **Disconnect all**, lalu Ctrl+C dashboard, lalu hentikan Appium.

## Urutan singkat (hafalan)

```
env → Appium → diagnose --live-test → dashboard → Connect all
```

## Saat berhenti

1. Dashboard: **Disconnect all** → Ctrl+C.
2. Tutup window Appium (atau langkah 1 di atas).
3. Jangan `adb kill-server` selama session masih dipakai.

## Kalau ada masalah

| Gejala | Lihat |
|---|---|
| `Connection refused` di port 4723–4725 | Appium belum jalan → langkah 3 |
| `Neither ANDROID_HOME nor ANDROID_SDK_ROOT` | env Appium → langkah 2–3 |
| `Could not find a connected Android device` | UDID vs `adb devices` → langkah 2, lalu `generate_rack.py --force` |
| `appium.ps1 cannot be loaded` | pakai `appium.cmd` / `start-appium.bat` |
| `Appium-Python-Client is not installed` | interpreter beda → pakai `.\.venv\Scripts\python.exe` atau `start-dashboard.bat` |

Detail penyebab: `py .\diagnose.py --manifest rack-01.json --live-test`.
