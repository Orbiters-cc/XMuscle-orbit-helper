@echo off
setlocal

set "ROOT=%~dp0"
set "DIST=%ROOT%dist"
set "ZIP=%DIST%\xmuscle_orbit_helper_clean.zip"

if not exist "%DIST%" mkdir "%DIST%"
if exist "%ZIP%" del "%ZIP%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path '%ROOT%xmuscle_orbit_helper' -DestinationPath '%ZIP%' -Force"

if errorlevel 1 (
  echo Failed to build addon zip.
  exit /b 1
)

echo Built %ZIP%
