@echo off
rem Start one Appium server per phone on Windows.
rem
rem Usage:
rem   start-appium.bat                      -> ports 4723 4724 4725
rem   start-appium.bat 4723 4724 4725 4726  -> explicit list
rem
rem Each server runs in its own cmd window, so the PowerShell execution
rem policy that blocks the npm appium.ps1 shim does not get in the way.

setlocal
set "PORTS=%*"
if "%PORTS%"=="" set "PORTS=4723 4724 4725"

where appium >nul 2>nul
if errorlevel 1 (
  echo appium tidak ditemukan. Install Node.js LTS lalu jalankan:
  echo     npm install -g appium
  exit /b 1
)

echo ANDROID_HOME=%ANDROID_HOME%
echo ANDROID_SDK_ROOT=%ANDROID_SDK_ROOT%
echo JAVA_HOME=%JAVA_HOME%
echo.

if "%ANDROID_HOME%"=="" if "%ANDROID_SDK_ROOT%"=="" (
  echo PERINGATAN: ANDROID_HOME dan ANDROID_SDK_ROOT kosong di window ini.
  echo Appium akan gagal dengan pesan "Neither ANDROID_HOME nor ANDROID_SDK_ROOT".
  echo Set dulu di window ini, contoh:
  echo     set "ANDROID_HOME=C:\Android\sdk"
  echo lalu jalankan ulang script ini ^(variabel User perlu window baru^).
  echo.
)
if "%JAVA_HOME%"=="" (
  echo PERINGATAN: JAVA_HOME kosong. Driver uiautomator2 juga membutuhkan JDK 17+.
  echo.
)

for %%p in (%PORTS%) do (
  echo Starting Appium on port %%p ...
  start "appium-%%p" cmd /k appium -p %%p
)

echo.
echo Appium windows started for ports: %PORTS%
echo Cek dengan: curl http://127.0.0.1:4723/status
endlocal
