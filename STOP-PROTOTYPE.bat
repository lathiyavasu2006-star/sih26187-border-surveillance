@echo off
rem Double-click this file to stop the backend, the operator console and the ML pipeline
rem that START-PROTOTYPE.bat launched (useful if that window was closed without pressing Q).
title SIH26187 - stopping the prototype
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_prototype.ps1" -Stop
rem A short pause so the result stays readable (ping, not timeout: timeout fails when stdin is redirected).
ping -n 4 127.0.0.1 >nul
