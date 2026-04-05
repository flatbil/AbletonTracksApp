@echo off
cd /d "%~dp0"
echo Starting AbletonAppPad Bridge...
echo Make sure Ableton is open with AbletonOSC loaded.
echo.
python -m bridge.main
pause
