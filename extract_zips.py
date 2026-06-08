import os
import zipfile

target_dir = r"F:\School\力致\力致_ref\00_ref\給成大資料\MODULE"

def extract_all_zips(directory):
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith('.zip'):
                zip_path = os.path.join(root, file)
                print(f"Extracting: {zip_path}")
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        # Extract everything to the directory where the zip file is located
                        zip_ref.extractall(root)
                    print(f"  -> Success")
                except Exception as e:
                    print(f"  -> Failed: {e}")

if __name__ == "__main__":
    print(f"Starting extraction in: {target_dir}")
    if os.path.exists(target_dir):
        extract_all_zips(target_dir)
        print("Done!")
    else:
        print(f"Directory does not exist: {target_dir}")
