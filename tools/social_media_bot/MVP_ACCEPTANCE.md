# MVP Acceptance — P0.1

Checklist ini dipakai untuk acceptance di device nyata. Jangan jalankan aksi
massal dengan akun produksi; gunakan akun uji dan target uji.

## Preflight environment

Jalankan di komputer dashboard:

```bash
adb devices -l
curl --max-time 2 http://127.0.0.1:4723/status
```

Lalu buka dashboard, connect device, dan pastikan app media sosial sudah login
manual.

## Status preflight — 18 November 2026

- `adb devices -l`: **tidak ada device terpasang**.
- Appium `192.168.1.20:4723`: **timeout**.
- Tidak ada aksi destruktif yang dijalankan.
- **Status P0.1: BLOCKED** sampai rack/device dan Appium tersedia.

## Matriks acceptance

Isi `PASS`/`FAIL`/`N/A`; catat versi aplikasi dan tipe HP.

| Platform | Versi app | Connect | Follow | Post teks | Comment/Reply | Like | Save | Catatan |
|---|---|---|---|---|---|---|---|---|
| Facebook |  |  |  |  |  |  |  |  |
| Instagram |  |  |  | N/A (media) |  |  |  |  |
| Threads |  |  |  |  |  |  |  |  |
| X |  |  |  |  |  |  |  |  |
| TikTok |  |  |  | N/A (video) |  |  |  |  |

## Aturan eksekusi

- Mulai dari satu device dan satu akun uji.
- Jalankan aksi satu per satu; jangan langsung bulk.
- Jika muncul checkpoint/captcha/2FA, hentikan pengujian akun tersebut.
- Catat screenshot error dan versi aplikasi sebelum memperbaiki locator.
- Setelah semua baris PASS, baru jalankan bulk/campaign kecil (2–3 item).

## Definition of done

- Semua aksi yang didukung di capability matrix PASS minimal di satu device
  dan satu versi aplikasi target.
- Locator yang gagal sudah diperbaiki atau aksi ditandai N/A dengan alasan.
- Hasil dicatat di tabel ini dan dihubungkan ke commit perbaikan.
