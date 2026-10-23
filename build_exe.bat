@echo off
rem Build dist\RehabGlove.exe (runs on PCs without Python; Chrome or Edge still required)
rem If PyInstaller fails on a very new Python, install Python 3.13 and run this again.
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% -m pip install --upgrade pyinstaller || goto :fail
%PY% -m PyInstaller --noconfirm --onefile --name RehabGlove --icon app.ico --add-data "web;web" thai_id_bridge.py || goto :fail
echo.
echo Done: dist\RehabGlove.exe
pause
exit /b 0
:fail
echo.
echo Build failed. See the messages above.
pause
exit /b 1
