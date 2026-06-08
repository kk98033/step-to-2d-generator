@echo off
chcp 65001 >nul
echo ===================================================
echo   正在為您製作 FORCECON 綠色免安裝版環境
echo ===================================================
echo.
echo [步驟 1/2] 啟動 Conda 環境並確認打包工具...

set CONDA_ACTIVATE="%USERPROFILE%\anaconda3\Scripts\activate.bat"
if exist %CONDA_ACTIVATE% goto DO_ACTIVATE

set CONDA_ACTIVATE="C:\ProgramData\anaconda3\Scripts\activate.bat"
if exist %CONDA_ACTIVATE% goto DO_ACTIVATE

set CONDA_ACTIVATE="%USERPROFILE%\miniconda3\Scripts\activate.bat"
if exist %CONDA_ACTIVATE% goto DO_ACTIVATE

call conda activate pyoccenv
goto INSTALL_PACK

:DO_ACTIVATE
call %CONDA_ACTIVATE% pyoccenv

:INSTALL_PACK
call conda install -c conda-forge conda-pack -y

echo.
echo [步驟 2/2] 正在將 pyoccenv 環境打包成 pyoccenv.zip 
echo (這可能會花費 3~5 分鐘，請耐心等候)...
if exist "%~dp0pyoccenv.zip" del "%~dp0pyoccenv.zip"
call conda pack -n pyoccenv -o "%~dp0pyoccenv.zip" --ignore-editable-packages

echo.
echo ===================================================
echo   環境打包完成！
echo.
echo   【新電腦的轉移步驟】：
echo   1. 將整個專案資料夾 (app) 帶到新電腦。
echo   2. 在新電腦中，把剛剛產生的 pyoccenv.zip 解壓縮，
echo      並把解壓縮出來的資料夾命名為 pyoccenv。
echo   3. 點擊執行「綠色版一鍵啟動.bat」，系統就會自動啟動！
echo ===================================================
pause
