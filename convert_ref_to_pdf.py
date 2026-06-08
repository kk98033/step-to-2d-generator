import os
import glob
import subprocess
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

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
output_dir = r"f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output"
temp_dxf_dir = os.path.join(input_dir, "temp_dxf_cache_ref")
pending_dwg_dir = os.path.join(input_dir, "pending_dwg_cache_ref")

# 建立資料夾
os.makedirs(output_dir, exist_ok=True)
os.makedirs(temp_dxf_dir, exist_ok=True)
os.makedirs(pending_dwg_dir, exist_ok=True)

# 遞迴尋找所有 DWG 檔案並記錄相對路徑
all_dwg_files = []
# 對應檔名到原始相對路徑 (假設檔名不重複)
filename_to_reldir = {}

for root, _, files in os.walk(input_dir):
    # 不要掃描我們自己建的暫存區與輸出區
    if "pending_dwg_cache_ref" in root or "temp_dxf_cache_ref" in root:
        continue
        
    for f in files:
        if f.lower().endswith('.dwg'):
            full_path = os.path.join(root, f)
            all_dwg_files.append(full_path)
            rel_dir = os.path.relpath(root, input_dir)
            filename_to_reldir[f] = rel_dir

if not all_dwg_files:
    print(f"在 {input_dir} 中找不到任何 DWG 檔案。")
    exit(0)

# 3. 過濾已經處理好的檔案 (檢查對應的子資料夾內是否已經有 PDF 和 DXF)
dwg_files_to_process = []
for dwg in all_dwg_files:
    base_name = os.path.basename(dwg)
    rel_dir = filename_to_reldir[base_name]
    target_folder = os.path.join(output_dir, rel_dir)
    
    pdf_filename = os.path.splitext(base_name)[0] + ".pdf"
    dxf_filename = os.path.splitext(base_name)[0] + ".dxf"
    
    pdf_path = os.path.join(target_folder, pdf_filename)
    dxf_path = os.path.join(target_folder, dxf_filename)
    
    if os.path.exists(pdf_path) and os.path.exists(dxf_path):
        print(f"⏭️ 略過已完成的檔案: {rel_dir}\\{base_name}")
    else:
        dwg_files_to_process.append(dwg)

if not dwg_files_to_process:
    print("\n🎉 所有檔案都已經有對應的 PDF 和 DXF 了，沒有需要轉換的檔案！")
    shutil.rmtree(pending_dwg_dir, ignore_errors=True)
    exit(0)

print(f"\n✅ 找到 {len(dwg_files_to_process)} 個待處理的 DWG 檔案。")

# 清空 pending 資料夾
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

print("⏳ 第二步：將 DXF 歸檔到對應資料夾並使用 ezdxf 繪製高解析度 PDF...")
dxf_files = glob.glob(os.path.join(temp_dxf_dir, "*.dxf"))

for dxf_file in dxf_files:
    base_dxf_name = os.path.basename(dxf_file)
    original_dwg_name = os.path.splitext(base_dxf_name)[0] + ".dwg"
    
    # 找回相對路徑
    rel_dir = filename_to_reldir.get(original_dwg_name, "")
    target_folder = os.path.join(output_dir, rel_dir)
    os.makedirs(target_folder, exist_ok=True)
    
    pdf_filename = os.path.splitext(base_dxf_name)[0] + ".pdf"
    output_pdf_path = os.path.join(target_folder, pdf_filename)
    output_dxf_path = os.path.join(target_folder, base_dxf_name)
    
    # 複製 DXF 過去 (儲存 DXF)
    if not os.path.exists(output_dxf_path):
        shutil.copy2(dxf_file, output_dxf_path)
    
    if os.path.exists(output_pdf_path):
        continue
        
    print(f"正在處理: {rel_dir}\\{base_dxf_name} -> PDF ...", end=" ", flush=True)
    
    # 將繪圖邏輯放在獨立的 subprocess 執行，避免單一檔案崩潰
    render_script = f"""import ezdxf, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import sys
try:
    doc = ezdxf.readfile(r'{output_dxf_path}')
    msp = doc.modelspace()
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    fig.savefig(r'{output_pdf_path}', dpi=300, bbox_inches='tight')
    plt.close(fig)
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    exit(1)
"""
    try:
        result = subprocess.run([r'E:\miniconda\envs\pyoccenv\python.exe', '-c', render_script], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        if result.returncode == 0:
            print("✅ 成功！")
        else:
            print(f"❌ 失敗！錯誤: {result.stderr.strip() or result.stdout.strip() or '未知的嚴重錯誤(可能記憶體不足)'}")
    except subprocess.TimeoutExpired:
        print("❌ 失敗！繪圖超時 (超過 120 秒)")

print(f"\n🎉 轉換完成！所有的 DXF 與 PDF 已經按照原結構放入 {output_dir}")
print("👉 請重整您的前端網頁，您就會在「業主範例圖 (Reference)」列表中看到它們了！")
