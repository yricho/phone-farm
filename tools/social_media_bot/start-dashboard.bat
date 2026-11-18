@echo off
rem Start the phone-farm dashboard on Windows.
rem
rem Usage:
rem   start-dashboard.bat
rem   start-dashboard.bat --manifest rack-lab.json --workers 4
rem   start-dashboard.bat --host 0.0.0.0 --port 8080  (wajib GADS_DASHBOARD_TOKEN)
rem
rem Ctrl+C menghentikan dashboard. Script memakai .venv milik folder ini bila ada,
rem supaya Appium-Python-Client tidak "hilang" karena interpreter berbeda.

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=py"
)

if not exist "rack-01.json" (
  echo Catatan: rack-01.json belum ada; dashboard akan start dalam mode setup.
)

echo Menjalankan dashboard dengan %PY% ...
echo Buka http://127.0.0.1:8080  ^(Ctrl+C untuk berhenti^)
"%PY%" dashboard.py --manifest rack-01.json --workers 3 %*
if errorlevel 1 pause
endlocal
