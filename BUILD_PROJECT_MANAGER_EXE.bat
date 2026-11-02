@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Build Project Manager Portable EXE

echo ============================================================
echo  Project Manager - Windows portable EXE builder
echo ============================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found.
    echo Install 64-bit Python 3.11 or newer and enable the Python Launcher.
    pause
    exit /b 1
)

if not exist ".buildenv\Scripts\python.exe" (
    echo Creating isolated build environment...
    py -3 -m venv .buildenv
    if errorlevel 1 goto :failed
)

echo Installing or updating PyInstaller...
call ".buildenv\Scripts\python.exe" -m pip install --upgrade pip pyinstaller
if errorlevel 1 goto :failed

if exist "build\ProjectManager" rmdir /s /q "build\ProjectManager"
if exist "dist\ProjectManager" rmdir /s /q "dist\ProjectManager"
if exist "ProjectManager.spec" del /q "ProjectManager.spec"

echo Building the portable Windows application...
call ".buildenv\Scripts\pyinstaller.exe" ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name ProjectManager ^
  --contents-directory _internal ^
  "ProjectManager.py"
if errorlevel 1 goto :failed

copy /y "README.md" "dist\ProjectManager\README.md" >nul
copy /y "THIRD_PARTY_NOTICES.txt" "dist\ProjectManager\THIRD_PARTY_NOTICES.txt" >nul

if not exist "release" mkdir "release"
if exist "release\ProjectManager_Portable.zip" del /q "release\ProjectManager_Portable.zip"

echo Creating release ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path '%~dp0dist\ProjectManager\*' -DestinationPath '%~dp0release\ProjectManager_Portable.zip' -Force"
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo  Build complete
echo  EXE: %~dp0dist\ProjectManager\ProjectManager.exe
echo  ZIP: %~dp0release\ProjectManager_Portable.zip
echo ============================================================
echo.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. Review the messages above.
pause
exit /b 1
