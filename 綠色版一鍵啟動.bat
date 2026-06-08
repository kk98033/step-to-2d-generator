@echo off
chcp 65001 >nul
echo ==============================================
echo   FORCECON Auto 2D Drawing System (綠色版)
echo ==============================================
echo 系統正在啟動後端伺服器，請稍候...

set PORTABLE_PYTHON="%~dp0pyoccenv\python.exe"

if not exist %PORTABLE_PYTHON% goto ERROR

start http://localhost:8000
cd /d "%~dp0web_app\backend"
call %PORTABLE_PYTHON% server.py
pause
exit /b

:ERROR
echo [錯誤] 找不到綠色版環境！
echo 請確保你已經將 pyoccenv.zip 解壓縮，並將資料夾命名為 pyoccenv，
echo 且該資料夾必須與此腳本放在同一個目錄下。
pause
exit /b
