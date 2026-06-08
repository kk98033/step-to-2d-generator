import os
import zipfile

def zipdir(path, ziph):
    print("開始掃描並壓縮檔案...")
    for root, dirs, files in os.walk(path):
        # 排除不需要打包的巨型與快取資料夾 (node_modules, __pycache__, .git 等)
        dirs[:] = [d for d in dirs if d not in ['node_modules', '__pycache__', '.git', '.vite']]
        
        for file in files:
            if file.endswith('.zip') or file == 'zip_project.py':
                continue
            file_path = os.path.join(root, file)
            arcname = os.path.relpath(file_path, path)
            ziph.write(file_path, arcname)
            
if __name__ == '__main__':
    # 將 zip 建立在 app 的上一層目錄，避免把自己壓進去
    zip_path = r'c:\MCAS_LAB\力致\FORCECON_Auto2D_Project.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipdir(r'c:\MCAS_LAB\力致\app', zipf)
    print(f"打包完成！壓縮檔已儲存於：{zip_path}")
