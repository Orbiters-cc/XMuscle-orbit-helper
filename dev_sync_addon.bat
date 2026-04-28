@echo off
setlocal

set "ROOT=%~dp0"
set "SRC=%ROOT%xmuscle_orbit_helper"
set "DST=%APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\xmuscle_orbit_helper"

if exist "%DST%" rmdir /s /q "%DST%"
xcopy "%SRC%" "%DST%\" /e /i /y >nul

if errorlevel 1 (
  echo Failed to sync addon files.
  exit /b 1
)

echo Synced addon to %DST%
echo In Blender, run "Reload Scripts" from F3 to pick up changes without reinstalling the zip.
