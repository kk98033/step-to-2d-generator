@echo off
chcp 65001 >nul
echo Starting FORCECON Auto 2D Drawing System...
echo Please wait while the server starts...

start http://localhost:8000

cd /d "%~dp0backend"

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
