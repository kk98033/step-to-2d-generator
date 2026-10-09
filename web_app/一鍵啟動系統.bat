@echo off
chcp 65001 >nul
echo Starting FORCECON Auto 2D Drawing System...
echo Please wait while the server starts...

cd /d "%~dp0backend"

set LOCAL_PYTHON=%~dp0..\pyoccenv\python.exe
if exist "%LOCAL_PYTHON%" goto START_LOCAL_ENV

set CONDA_ACTIVATE="%USERPROFILE%\anaconda3\Scripts\activate.bat"
if exist %CONDA_ACTIVATE% goto DO_ACTIVATE

set CONDA_ACTIVATE="C:\ProgramData\anaconda3\Scripts\activate.bat"
if exist %CONDA_ACTIVATE% goto DO_ACTIVATE

set CONDA_ACTIVATE="%USERPROFILE%\miniconda3\Scripts\activate.bat"
if exist %CONDA_ACTIVATE% goto DO_ACTIVATE

echo Warning: Could not find conda activate.bat, trying raw conda command.
call conda activate pyoccenv
goto START_SERVER

:DO_ACTIVATE
call %CONDA_ACTIVATE% pyoccenv

:START_SERVER
python server.py
pause
goto :eof

:START_LOCAL_ENV
echo Using project-local pyoccenv...
start "" /min cmd /c "timeout /t 5 /nobreak ^>nul ^& start "" http://localhost:8000"
"%LOCAL_PYTHON%" server.py
pause
goto :eof
