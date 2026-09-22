@echo off
setlocal
rem Windows launcher for todoctl.py.
rem Python is resolved from PATH on purpose: no machine-specific paths in this repo.
set "PY=py -3"
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto run
set "PY=python"
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto run
goto nopython
:run
%PY% "%~dp0todoctl.py" %*
exit /b %ERRORLEVEL%
:nopython
echo [todoctl] Python 3 not found on PATH.
echo           Install Python 3.8+ from https://www.python.org/downloads/windows/
echo           and tick "Add python.exe to PATH" during setup.
exit /b 9009
