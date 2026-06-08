# Web App 啟動指南

這個 Web App 包含前端 (React/Vite) 與後端 (Python/FastAPI) 兩個部分。請依照以下步驟分別啟動後端與前端伺服器。

## 1. 啟動後端 (Backend)

後端是使用 FastAPI 撰寫的 API 伺服器，負責處理 2D 圖面生成與模型讀取。

**步驟：**
1. 請開啟 **Anaconda Prompt** (不要使用一般的命令提示字元或 PowerShell)。
2. 啟動你的 Python 專屬環境 (你之前的名稱是 `pyoccenv`)：
   ```bash
   conda activate pyoccenv
   ```
3. 切換到 `backend` 目錄：
   ```bash
   cd c:\MCAS_LAB\力致\app\web_app\backend
   ```
4. 確保已安裝相關的 FastAPI 依賴套件 (如果還沒安裝過的話，可以執行 `pip install fastapi uvicorn python-multipart`)。
5. 執行以下指令啟動伺服器：
   ```bash
   python server.py
   ```
   > 或者也可以使用 uvicorn 啟動：`uvicorn server:app --host 0.0.0.0 --port 8000 --reload`
5. 當看到 `Uvicorn running on http://0.0.0.0:8000` 表示後端已成功啟動。

---

## 2. 啟動前端 (Frontend)

前端是使用 React + Vite 建構的使用者介面。

**步驟：**
1. 打開另一個新的終端機 (Terminal)。
2. 切換到 `frontend` 目錄：
   ```bash
   cd c:\MCAS_LAB\力致\app\web_app\frontend
   ```
3. 如果是第一次啟動，請先安裝依賴套件 (確保你已經安裝了 Node.js 與 npm)：
   ```bash
   npm install
   ```
4. 啟動 Vite 開發伺服器：
   ```bash
   npm run dev
   ```
5. 終端機中會顯示前端的本機網址 (通常是 `http://localhost:5173`)。按住 `Ctrl` 鍵並點擊該網址，即可在瀏覽器中開啟 Web App。

## 注意事項
- 請確保後端 (Port 8000) 和前端 (通常是 Port 5173) **同時處於執行狀態**，否則前端可能會無法正常取得後端 API 的資料。
