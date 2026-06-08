"""
DXF -> PDF 批次轉換器 (穩健版)
掃描 example_output 目錄下已有的 DXF 檔，補齊缺少的 PDF。
每個檔案在獨立 subprocess 執行，記憶體爆掉不影響其他檔案。
"""
import os
import sys
import glob
import subprocess
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PYTHON_EXE = r'E:\miniconda\envs\pyoccenv\python.exe'
EXAMPLE_DIR = r'f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output'

if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable # fallback to current python

# 找出所有缺少 PDF 的 DXF 檔
dxf_files = []
for root, _, files in os.walk(EXAMPLE_DIR):
    for f in files:
        if f.lower().endswith('.dxf'):
            dxf_path = os.path.join(root, f)
            pdf_path = os.path.splitext(dxf_path)[0] + '.svg'
            if not os.path.exists(pdf_path):
                dxf_files.append(dxf_path)

total = len(dxf_files)
if total == 0:
    print("🎉 所有 DXF 檔都已有對應的 SVG，沒有需要轉換的檔案！")
    sys.exit(0)

print(f"📋 找到 {total} 個缺少 PDF 的 DXF 檔，開始批次轉換...\n")

success = 0
failed = 0
skipped = 0
start_time = time.time()

for idx, dxf_path in enumerate(dxf_files, 1):
    rel_path = os.path.relpath(dxf_path, EXAMPLE_DIR)
    pdf_path = os.path.splitext(dxf_path)[0] + '.svg'
    
    print(f"[{idx}/{total}] {rel_path} ... ", end="", flush=True)
    
    try:
        worker_script = os.path.join(os.path.dirname(__file__), 'render_worker.py')
        result = subprocess.run(
            [PYTHON_EXE, worker_script, dxf_path, pdf_path],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=180  # 3 分鐘超時
        )
        if result.returncode == 0:
            print("✅")
            success += 1
        else:
            err_msg = result.stderr.strip() or result.stdout.strip() or '未知錯誤'
            if len(err_msg) > 100:
                err_msg = err_msg[:100] + '...'
            print(f"❌ {err_msg}")
            failed += 1
    except subprocess.TimeoutExpired:
        print("⏱️ 超時(3分鐘)")
        failed += 1
    except Exception as e:
        print(f"💥 {e}")
        failed += 1

elapsed = time.time() - start_time
print(f"\n{'='*60}")
print(f"🏁 轉換完成！耗時: {elapsed:.0f} 秒")
print(f"   ✅ 成功: {success}")
print(f"   ❌ 失敗: {failed}")
print(f"   📄 總計: {total}")
print(f"{'='*60}")
