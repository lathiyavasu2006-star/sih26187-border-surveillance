@echo off
rem Double-click this file to start the whole SIH26187 prototype:
rem   backend API -> operator console -> ML pipeline, in that order.
rem Windows Terminal lets you Ctrl+Click the printed link; in the old console press O instead.
title SIH26187 - AI Border Surveillance Prototype
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_prototype.ps1"
if errorlevel 1 pause
