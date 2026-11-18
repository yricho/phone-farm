# MVP Priorities — SocialMediaBot

Diperbarui: 18 November 2026

Dokumen ini mengunci urutan prioritas menuju MVP. Fokus MVP yang
diasumsikan: **satu rack, akun sudah login manual, lima platform, aksi teks dan
engagement, dashboard localhost/LAN internal**.

## Aturan prioritas

1. **P0 adalah gate rilis.** Jangan menambah platform atau fitur besar sebelum
   semua P0 selesai.
2. **P1 tidak menghalangi demo internal**, tetapi wajib sebelum dipakai rutin.
3. **P2 adalah ekspansi** setelah MVP stabil di device nyata.
4. Setiap item selesai harus punya bukti: test, screenshot, atau catatan
   acceptance di device.

## P0 — wajib sebelum MVP dianggap selesai

### P0.1 Acceptance test di device nyata

**Kenapa:** test suite lulus, tetapi Appium test di-skip dan locator
like/save/reply masih generik.

**Lingkup:**
- Facebook, Instagram, Threads, X, TikTok.
- Minimal: connect, buka app, follow, comment/reply, like, save.
- Post teks untuk Facebook, Threads, X.
- Catat versi aplikasi, tipe HP, dan hasil per aksi.

**Selesai bila:** matriks acceptance per platform terisi, locator yang gagal
diperbaiki, dan tidak ada blocker koneksi/session.

**Status 18 Nov 2026: BLOCKED.** `adb devices` kosong dan Appium
`192.168.1.20:4723` timeout. Checklist ada di
[MVP_ACCEPTANCE.md](MVP_ACCEPTANCE.md); tidak ada aksi destruktif dijalankan.

### P0.2 Batas akses dashboard

**Kenapa:** dashboard belum punya autentikasi/CSRF. Default `127.0.0.1` aman,
tetapi `--host 0.0.0.0` membuka kontrol device ke LAN.

**Dipilih:** token login dashboard berbasis cookie.

- Bind non-loopback **ditolak** jika `--auth-token`/`GADS_DASHBOARD_TOKEN`
  kosong.
- `GET /` menampilkan halaman login; token valid membuat cookie HttpOnly,
  SameSite=Strict.
- API juga menerima `Authorization: Bearer <token>`.
- Default localhost tetap tanpa login.

**Status: SELESAI 18 Nov 2026.** Test login/token ada di
`tests/test_dashboard.py` (`DashboardAuthTests`).

### P0.3 Keputusan media posting

**Kenapa:** Instagram dan TikTok belum mendukung posting teks; posting butuh
flow media picker/upload.

**Diputuskan:** media posting (foto/video) **di luar scope MVP** untuk
Instagram/TikTok. UI dan API tetap menolak aksi post pada kedua platform sampai
flow media tersedia.

**Status: SELESAI 18 Nov 2026.**

### P0.4 UI regression smoke test di repo

**Kenapa:** smoke test theme, accordion, dan ikon masih berupa script sementara.

**Lingkup:**
- cek theme dark/light + persistensi;
- cek exclusive accordion (`maxOpen = 1`);
- cek CTA icons dan label dinamis;
- skip otomatis bila Chrome tidak tersedia.

**Status: SELESAI 18 Nov 2026.** Test ada di `tests/test_ui_smoke.py` dan
di-skip otomatis bila Chrome/Chromium tidak ditemukan.

## P1 — polish sebelum pemakaian rutin

### P1.1 Accessibility quick wins

- `aria-pressed` untuk tombol Single/Bulk.
- `role="status" aria-live="polite"` untuk toast.
- `role="alert"` untuk banner offline.
- `:focus-visible` konsisten untuk semua `.btn`.
- Perbaiki kontras teks kecil light mode dan border input.

**Selesai bila:** keyboard navigation jelas dan status async diumumkan.

### P1.2 Ketahanan polling dan render

- Guard agar `tick()` tidak overlap.
- Timeout/AbortController untuk request.
- Auto-scroll log hanya jika user berada di bottom.
- Stabilkan render job agar fokus tombol Cancel/Retry tidak hilang.

### P1.3 Polish kecil

- Favicon dari brand icon.
- Konsistensi bahasa label UI.
- Perbesar hit area tombol ikon kecil bila perlu.
- Prune `state.notifiedJobs`.

## P2 — setelah MVP stabil

- Platform tambahan: YouTube, LinkedIn, Reddit, Pinterest, Telegram.
- API connector: Bluesky, Mastodon, Discord.
- Media library dan penjadwalan media.
- Multi-rack orchestration dari satu dashboard.
- Analytics historis dan export.
- Login/checkpoint detection otomatis.

## Release checklist

- [ ] Semua P0 selesai.
- [x] `python3 -m unittest discover -s tools/social_media_bot/tests` lulus
  (159 test, 1 Appium test di-skip).
- [x] Smoke test UI dark/light, accordion, dan CTA lulus.
- [ ] Acceptance device nyata tercatat (P0.1 blocked: belum ada device).
- [ ] Dashboard di-restart dan hard-refresh setelah deploy.
- [ ] Tidak ada `*.state.json` atau proses test yang tertinggal.

## Status saat ini

**Sudah:** dashboard single-rack, live screen, setup rack, lima platform,
follow/post teks/comment/like/save/reply sesuai capability, campaign,
scheduler, daily limits, analytics, health check, notification, dark/light
theme, ikon CTA, exclusive accordion, token login untuk akses non-loopback,
smoke test UI di repo, dan **159 test lulus** (1 Appium test di-skip).

**Berikutnya:** P0.1 acceptance device nyata — saat ini **BLOCKED** karena tidak
ada device ADB dan Appium `192.168.1.20:4723` timeout.
