@echo off
rem Open RehabGlove Clinic (app window + Thai ID card reader)
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
start "RehabGlove Clinic (close this window to quit)" /min cmd /c "chcp 65001 >nul & %PY% thai_id_bridge.py --open || pause"
