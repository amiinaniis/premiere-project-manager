@echo off
setlocal
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0ProjectManager.py" %*
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0ProjectManager.py" %*
    exit /b 0
)

msg * Python was not found. Run BUILD_PROJECT_MANAGER_EXE.bat on a Windows PC with Python installed, or install Python from python.org.
exit /b 1
