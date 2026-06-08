# 🛠️ STEP to 2D Generator Web Application

這是一個基於前後端分離架構的 Web 應用程式，旨在提供 3D 模型 (STEP) 解析與 2D 圖面自動生成的圖形化介面。

- **Frontend**: React + Vite
- **Backend**: Python + FastAPI + pythonocc-core

---

## 🚀 快速啟動指南 (Quick Start)

為了讓系統正常運行，請確保**後端**與**前端**伺服器皆處於執行狀態。請開啟兩個獨立的終端機視窗來分別執行它們。

### 1. 後端伺服器 (Backend API)

後端負責核心的 3D 模型解析及圖面轉換，依賴 `pythonocc-core`，請務必在 Conda 虛擬環境中執行。

**啟動步驟：**
1. 開啟 **Anaconda Prompt** (建議使用，以確保環境變數正確)。
2. 啟動專屬的 Python 虛擬環境：
   ```bash
   conda activate pyoccenv
   ```
3. 切換至後端程式目錄：
   ```bash
   cd web_app/backend
   ```
4. 安裝後端相依套件 (若為首次執行)：
   ```bash
   pip install fastapi uvicorn python-multipart ezdxf
   ```
5. 啟動 FastAPI 伺服器：
   ```bash
   python server.py
   ```
   *(當終端機顯示 `Uvicorn running on http://0.0.0.0:8000` 時，代表伺服器已成功啟動)*

> **💡 開發者提示**：若需要程式碼熱重載 (Hot Reload) 功能，可改用以下指令啟動：
> `uvicorn server:app --host 0.0.0.0 --port 8000 --reload`

---

### 2. 前端介面 (Frontend UI)

前端介面提供使用者上傳模型及設定參數的互動視窗。

**啟動步驟：**
1. 開啟另一個新的終端機視窗。
2. 切換至前端程式目錄：
   ```bash
   cd web_app/frontend
   ```
3. 安裝前端相依套件 (若為首次執行，請確認已安裝 Node.js)：
   ```bash
   npm install
   ```
4. 啟動 Vite 本機開發伺服器：
   ```bash
   npm run dev
   ```
5. 終端機將顯示服務網址（預設為 `http://localhost:5173`）。按住 `Ctrl` 鍵並點擊該連結，即可於瀏覽器中開啟系統。

---

## ⚠️ 注意事項 (Important Notes)

- **前後端連動**：請確保後端 API 伺服器 (Port 8000) 與前端伺服器 (Port 5173) **同時處於執行狀態**，否則前端將無法正常存取資料。
- **路徑設定**：請確保在執行啟動指令時，終端機的當前目錄已正確切換至 `backend` 或 `frontend` 目錄下。
