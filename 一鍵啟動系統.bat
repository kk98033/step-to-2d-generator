@echo off
chcp 65001 >nul
title FORCECON Auto 2D CAD Generator - 一鍵啟動系統
echo ================================================================
echo   FORCECON STEP-to-2D 智慧工程圖產生器 (綠色免安裝版)
echo ================================================================
echo.

set ROOT_DIR=%~dp0
set PORTABLE_PYTHON=%ROOT_DIR%pyoccenv\python.exe

:: 檢查是否有現成的 pyoccenv 目錄
if exist "%PORTABLE_PYTHON%" goto START_SERVER

:: 若沒有 pyoccenv 但有 pyoccenv.zip，自動進行解壓縮
if exist "%ROOT_DIR%pyoccenv.zip" (
    echo [提示] 首次執行檢測到 pyoccenv.zip，正在自動解壓縮虛擬環境...
    echo 請稍候約 30~60 秒，解壓完成後將自動啟動系統...
    powershell -Command "Expand-Archive -Path '%ROOT_DIR%pyoccenv.zip' -DestinationPath '%ROOT_DIR%' -Force"
    if exist "%PORTABLE_PYTHON%" goto START_SERVER
)

:: 若解壓失敗或無環境包
echo [錯誤] 找不到 PythonOCC 運行環境！
echo 請確認目錄下存在 pyoccenv 資料夾或 pyoccenv.zip。
echo.
pause
exit /b 1

:START_SERVER
echo [1/2] 正在啟動後端 FastAPI 智慧標註運算服務...
echo 伺服器網址: http://localhost:8000
echo.

:: 延遲 2 秒後自動開啟瀏覽器
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

:: 啟動伺服器
cd /d "%ROOT_DIR%web_app\backend"
"%PORTABLE_PYTHON%" -m uvicorn server:app --host 0.0.0.0 --port 8000

pause
