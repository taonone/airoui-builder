@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
python "%~dp0scripts\configure_mobile_signing.py" --common --setup --repo taonone/airoui-builder
set "foundry_signing_result=%ERRORLEVEL%"
echo.
pause
exit /b %foundry_signing_result%
