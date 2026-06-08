import os
import glob
import subprocess
import shutil

try:
    import ezdxf
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
except ImportError:
    print("錯誤: 缺少必要的套件。")
    print("請先在終端機執行: pip install ezdxf matplotlib")
    exit(1)

# 1. 尋找 ODA File Converter 路徑
oda_paths = glob.glob(r"C:\Program Files\ODA\ODAFileConverter*\ODAFileConverter.exe")
if not oda_paths:
    oda_paths = glob.glob(r"C:\Program Files (x86)\ODA\ODAFileConverter*\ODAFileConverter.exe")

if not oda_paths:
    print("❌ 錯誤: 找不到 ODA File Converter！請確認是否已安裝在預設路徑。")
    exit(1)

oda_exe = oda_paths[0]

# 2. 路徑設定
input_dir = r"f:\School\力致\app\models\AJ0A-軸流扇\BOM及2D圖面"
output_dir = os.path.join(input_dir, "PDF輸出")
temp_dxf_dir = os.path.join(input_dir, "temp_dxf_cache")
pending_dwg_dir = os.path.join(input_dir, "pending_dwg_cache") # 新增：存放待處理的 DWG

# 建立資料夾
os.makedirs(output_dir, exist_ok=True)
os.makedirs(temp_dxf_dir, exist_ok=True)
os.makedirs(pending_dwg_dir, exist_ok=True)

all_dwg_files = glob.glob(os.path.join(input_dir, "*.dwg"))
if not all_dwg_files:
    print(f"在 {input_dir} 中找不到任何 DWG 檔案。")
    exit(0)

# --- 新增: 過濾已經處理好的檔案 ---
dwg_files_to_process = []
for dwg in all_dwg_files:
    base_name = os.path.basename(dwg)
    pdf_filename = os.path.splitext(base_name)[0] + ".pdf"
    if os.path.exists(os.path.join(output_dir, pdf_filename)):
        print(f"⏭️ 略過已完成的檔案: {base_name}")
    else:
        dwg_files_to_process.append(dwg)

if not dwg_files_to_process:
    print("\n🎉 所有檔案都已經有對應的 PDF 了，沒有需要轉換的檔案！")
    # 清理 pending 資料夾
    shutil.rmtree(pending_dwg_dir, ignore_errors=True)
    exit(0)

print(f"\n✅ 找到 {len(dwg_files_to_process)} 個待處理的 DWG 檔案。")
print(f"🔧 使用的 ODA 轉換器路徑: {oda_exe}")

# 將待處理的 DWG 複製到 pending 資料夾，以便 ODA 只轉換這些檔案
# 清空 pending 資料夾避免殘留
for f in glob.glob(os.path.join(pending_dwg_dir, "*")):
    os.remove(f)

for dwg in dwg_files_to_process:
    shutil.copy2(dwg, os.path.join(pending_dwg_dir, os.path.basename(dwg)))

print("⏳ 第一步：呼叫 ODA 將未完成的 DWG 批次轉為 DXF (這可能需要幾秒鐘)...")

# 3. ODA 指令參數: InputFolder OutputFolder Version OutputFormat Recurse Audit [InputFilesFilter]
cmd = [
    oda_exe,
    pending_dwg_dir,
    temp_dxf_dir,
    "ACAD2018", # 輸出的 DXF 版本
    "DXF",
    "0",        # 不遞迴子目錄
    "1",        # 開啟 Audit 修復
    "*.dwg"
]

try:
    subprocess.run(cmd, check=True, capture_output=True)
    print("✅ DWG 成功轉為 DXF！\n")
except subprocess.CalledProcessError as e:
    print(f"❌ ODA 轉換失敗: {e.stderr.decode('utf-8', errors='ignore')}")
    # 即使 ODA 失敗，可能還是有部分檔案產出，我們繼續往下走

# 處理完就把 pending_dwg_dir 刪除
shutil.rmtree(pending_dwg_dir, ignore_errors=True)

print("⏳ 第二步：使用 ezdxf 繪製高解析度 PDF (保證無浮水印)...")
dxf_files = glob.glob(os.path.join(temp_dxf_dir, "*.dxf"))

for dxf_file in dxf_files:
    base_name = os.path.basename(dxf_file)
    pdf_filename = os.path.splitext(base_name)[0] + ".pdf"
    output_path = os.path.join(output_dir, pdf_filename)
    
    # 如果 PDF 已經存在就不重畫
    if os.path.exists(output_path):
        continue
        
    print(f"正在處理: {base_name} -> {pdf_filename} ...", end=" ", flush=True)
    
    try:
        # 讀取 DXF
        doc = ezdxf.readfile(dxf_file)
        msp = doc.modelspace()
        
        # 建立 Matplotlib 畫布
        fig = plt.figure()
        ax = fig.add_axes([0, 0, 1, 1])
        ctx = RenderContext(doc)
        out = MatplotlibBackend(ax)
        
        # 繪製佈局並關閉座標軸
        Frontend(ctx, out).draw_layout(msp, finalize=True)
        
        # 存檔為 PDF，設定 300 DPI
        fig.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        print("✅ 成功！")
    except Exception as e:
        print(f"❌ 失敗！錯誤訊息: {e}")

# (使用者要求: 暫存檔 DXF 不要刪除，以便後續可以保留或分析)
# try:
#     shutil.rmtree(temp_dxf_dir)
#     print("\n🧹 已清理過渡用的 DXF 暫存檔。")
# except Exception:
#     pass

print(f"\n🎉 處理階段結束！請到 {output_dir} 查看您乾淨無浮水印的 PDF 檔案。")
print("（暫存的 DXF 檔案已依照您的要求保留在 temp_dxf_cache 中）")
