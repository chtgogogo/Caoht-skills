@echo off
chcp 65001 >nul
cd /d "%~dp0scripts"

set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python not found. Please install Python 3.10+.
  pause
  exit /b 1
)

%PY% dy_export.py
pause
