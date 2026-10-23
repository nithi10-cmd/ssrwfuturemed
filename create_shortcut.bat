@echo off
rem Create "RehabGlove Clinic" shortcuts on the Desktop and in the Start menu
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$shell = New-Object -ComObject WScript.Shell;" ^
 "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
 "  $s = $shell.CreateShortcut((Join-Path $dir 'RehabGlove Clinic.lnk'));" ^
 "  $s.TargetPath = (Join-Path (Get-Location) 'RehabGlove.bat');" ^
 "  $s.WorkingDirectory = (Get-Location).Path;" ^
 "  $s.IconLocation = (Join-Path (Get-Location) 'app.ico');" ^
 "  $s.WindowStyle = 7;" ^
 "  $s.Description = 'RehabGlove Clinic';" ^
 "  $s.Save() }"
echo.
echo Created "RehabGlove Clinic" shortcut on the Desktop and in the Start menu.
pause
