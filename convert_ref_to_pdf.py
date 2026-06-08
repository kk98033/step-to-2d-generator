import os
import glob
import subprocess
import shutil

try:
    import ezdxf
    import matplotlib
    matplotlib.use('Agg')
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
input_dir = r"f:\School\力致\力致_ref"
# 網頁「業主參考的模型」所讀取的資料夾路徑
output_dir = r"f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output"
temp_dxf_dir = os.path.join(input_dir, "temp_dxf_cache_ref")
pending_dwg_dir = os.path.join(input_dir, "pending_dwg_cache_ref")

# 建立資料夾
os.makedirs(output_dir, exist_ok=True)
os.makedirs(temp_dxf_dir, exist_ok=True)
os.makedirs(pending_dwg_dir, exist_ok=True)

# 遞迴尋找所有 DWG 檔案
all_dwg_files = []
for root, _, files in os.walk(input_dir):
    for f in files:
        if f.lower().endswith('.dwg'):
            all_dwg_files.append(os.path.join(root, f))

if not all_dwg_files:
    print(f"在 {input_dir} 中找不到任何 DWG 檔案。")
    exit(0)

# 3. 過濾已經處理好的檔案 (集中到同一個 output_dir，所以要處理檔名衝突，但我們假設檔名唯一)
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
    shutil.rmtree(pending_dwg_dir, ignore_errors=True)
    exit(0)

print(f"\n✅ 找到 {len(dwg_files_to_process)} 個待處理的 DWG 檔案。")

# 將待處理的 DWG 集中複製到 pending 資料夾，以便 ODA 一次處理
for f in glob.glob(os.path.join(pending_dwg_dir, "*")):
    try: os.remove(f)
    except: pass

for dwg in dwg_files_to_process:
    target_path = os.path.join(pending_dwg_dir, os.path.basename(dwg))
    if not os.path.exists(target_path):
        shutil.copy2(dwg, target_path)

print("⏳ 第一步：呼叫 ODA 將未完成的 DWG 批次轉為 DXF...")
cmd = [
    oda_exe,
    pending_dwg_dir,
    temp_dxf_dir,
    "ACAD2018", "DXF", "0", "1", "*.dwg"
]

try:
    subprocess.run(cmd, check=True, capture_output=True)
    print("✅ DWG 成功轉為 DXF！\n")
except subprocess.CalledProcessError as e:
    print(f"❌ ODA 轉換失敗: {e.stderr.decode('utf-8', errors='ignore')}")

shutil.rmtree(pending_dwg_dir, ignore_errors=True)

print("⏳ 第二步：使用 ezdxf 繪製高解析度 PDF (保證無浮水印)...")
dxf_files = glob.glob(os.path.join(temp_dxf_dir, "*.dxf"))

for dxf_file in dxf_files:
    base_name = os.path.basename(dxf_file)
    pdf_filename = os.path.splitext(base_name)[0] + ".pdf"
    output_path = os.path.join(output_dir, pdf_filename)
    
    if os.path.exists(output_path):
        continue
        
    print(f"正在處理: {base_name} -> {pdf_filename} ...", end=" ", flush=True)
    
    # 將繪圖邏輯放在獨立的 subprocess 執行，避免單一檔案導致整個腳本崩潰
    render_script = f"""import ezdxf, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
try:
    doc = ezdxf.readfile(r'{dxf_file}')
    msp = doc.modelspace()
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    fig.savefig(r'{output_path}', dpi=300, bbox_inches='tight')
    plt.close(fig)
except Exception as e:
    print(f"ERROR: {{e}}")
    exit(1)
"""
    try:
        result = subprocess.run([r'E:\miniconda\envs\pyoccenv\python.exe', '-c', render_script], capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print("✅ 成功！")
        else:
            print(f"❌ 失敗！錯誤: {result.stderr.strip() or result.stdout.strip() or '未知的嚴重錯誤(可能記憶體不足)'}")
    except subprocess.TimeoutExpired:
        print("❌ 失敗！繪圖超時 (超過 120 秒)")

print(f"\n🎉 轉換完成！所有的 PDF 已經放入 {output_dir}")
print("👉 請重整您的前端網頁，您就會在「業主範例圖 (Reference)」列表中看到它們了！")
