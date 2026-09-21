import sys, os
sys.path.insert(0, r"D:\School\力致\app\step-to-2d-generator")
sys.path.insert(0, r"D:\School\力致\app\step-to-2d-generator\web_app\backend")

import uvicorn
from server import app

if __name__ == "__main__":
    print("Starting FastAPI Server on http://0.0.0.0:8000 ...", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
