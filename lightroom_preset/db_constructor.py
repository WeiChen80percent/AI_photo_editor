import os
import zipfile
import sqlite3
from pathlib import Path

SOURCE_DIR = 'C:/Users/User/Desktop/grad_project_datasets/lightroom_preset/datasets_zip'
DESCRIPTION_DIR = 'C:/Users/User/Desktop/grad_project_datasets/lightroom_preset/datasets_description'
DB_NAME = 'presets_vault_unique.db'
MAX_FILE_SIZE_KB = 50  # 設定最大檔案限制：50 KB

def setup_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 建立資料表：儲存原始檔名、預設集名稱與完整的 XML/文字內容
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zip_filename TEXT,
            preset_name TEXT,
            content TEXT,
            description TEXT,
            UNIQUE(zip_filename, preset_name)
        )
    ''')
    conn.commit()
    return conn

def process_presets(conn):
    cursor = conn.cursor()
    source_path = Path(SOURCE_DIR)
    file_processed_count = 0
    
    for zip_path in source_path.glob('*.zip'):
        print(f"正在處理: {file_processed_count}. {zip_path.name}")
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                for file_info in z.infolist():
                    # 1. 檢查副檔名
                    if not file_info.is_dir() and file_info.filename.endswith(('.xmp')):
                        
                        # 2. 檢查檔案大小 (將 bytes 轉換為 KB)
                        file_size_kb = file_info.file_size / 1024
                        if file_size_kb > MAX_FILE_SIZE_KB:
                            print(f"  [跳過] 檔案過大 ({file_size_kb:.1f} KB)，可能是照片附屬檔: {file_info.filename}")
                            continue # 直接跳過，不寫入資料庫
                            
                        # 3. 讀取並寫入資料庫
                        with z.open(file_info) as f:
                            try:
                                content = f.read().decode('utf-8')
                                preset_name = os.path.basename(file_info.filename)
                                
                                cursor.execute(
                                    "INSERT INTO presets (zip_filename, preset_name, content) VALUES (?, ?, ?)",
                                    (zip_path.name, preset_name, content)
                                )
                            except UnicodeDecodeError:
                                print(f"  [跳過] 編碼錯誤: {file_info.filename}")
        except Exception as e:
            print(f"  [錯誤] 無法讀取 {zip_path.name}: {e}")

    conn.commit()
    print("\n 處理完成")
    
def process_descriptions(conn):
    cursor = conn.cursor()
    source_path = Path(DESCRIPTION_DIR)
    file_processed_count = 0
    for txt_path in source_path.glob('*.txt'):
        print(f"正在處理描述: {file_processed_count}. {txt_path.name}")
        
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                description = f.read()
                zipfile_name = f"{txt_path.stem}.zip"  # 取得檔名（不含副檔名）
                
                # 更新資料庫中的描述欄位
                cursor.execute(
                    "UPDATE presets SET description = ? WHERE zip_filename = ?",
                    (description, zipfile_name)
                )
        except Exception as e:
            print(f"  [錯誤] 無法讀取 {txt_path.name}: {e}")
    conn.commit()
    print("\n 描述處理完成")

if __name__ == "__main__":
    try:
        conn = setup_database()
        process_presets(conn)
        process_descriptions(conn)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if conn:
            conn.close()