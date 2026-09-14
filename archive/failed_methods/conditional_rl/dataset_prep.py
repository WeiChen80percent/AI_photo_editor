import os
import cv2
import pickle
import numpy as np
import base64
import subprocess
from pathlib import Path
from tqdm import tqdm

# 如果環境尚未安裝以下套件，請在 terminal 執行：
from sentence_transformers import SentenceTransformer
from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

# --- 參數設定 ---
# 為了快速驗證概念，我們先限制處理 100 張圖片
MAX_SAMPLES = 100
# 資料集路徑
DATA_DIR = Path("../data").resolve()
RAW_DIR = DATA_DIR / "raw"
EXPERT_DIRS = {"C": DATA_DIR / "c", "E": DATA_DIR / "e"}
OUTPUT_FILE = Path("dataset_features.pkl")

# VLM 與 Embedding 模型設定
VLM_MODEL_NAME = "moondream:latest"  # 或者用 "gemma3:4b" / "qwen2.5:7b" 視您機器的支援程度
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2" # 將文字轉為 384 維向量

def encode_image(image_path):
    """將圖片轉為 base64 格式，以供 VLM 讀取"""
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    
    # 將原始圖片縮小以加快 VLM 處理速度
    h, w = img.shape[:2]
    max_side = 512
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    
    _, buffer = cv2.imencode('.jpg', img)
    return base64.b64encode(buffer).decode('utf-8')

def calculate_histogram(image_path):
    """計算圖片的 RGB 色彩直方圖 (歸一化為一維特徵向量)"""
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    # 轉為 RGB
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
    # 計算 8x8x8 的 3D 直方圖 (共 512 維)
    hist = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    # 歸一化
    hist = cv2.normalize(hist, hist).flatten()
    return hist

def extract_image_context(vlm_model, base64_image):
    """請 VLM 診斷原始 RAW 圖片的狀態 (曝光、色溫、對比)"""
    prompt = "Briefly describe the exposure, lighting, and color balance of this photo in one short sentence."
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
        ]
    )
    try:
        response = vlm_model.invoke([message])
        return response.content.strip()
    except Exception as e:
        print(f"VLM Context Error: {e}")
        return "Normal exposure and lighting."

def extract_style_prompt(vlm_model, base64_expert_image):
    """請 VLM 觀察專家修圖後的結果，並模擬使用者生成一句『修圖指令 (Prompt)』"""
    prompt = (
        "This is an expertly color-graded photo. "
        "As a user asking an AI to replicate this specific style, write a short, one-sentence request. "
        "For example: 'Make it warm and cinematic with high contrast' or 'Give it a natural film look'."
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_expert_image}"}},
        ]
    )
    try:
        response = vlm_model.invoke([message])
        return response.content.strip()
    except Exception as e:
        print(f"VLM Style Error: {e}")
        return "Make it look professional."

def main():
    print("初始化模型中...")
    # 啟動ollama服務，確保 Ollama 正在運行 VLM 模型
    subprocess.run(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 初始化 VLM (確保 Ollama 正在運行此模型)
    vlm_model = ChatOllama(model=VLM_MODEL_NAME, temperature=0.1)
    # 初始化 Embedding Model
    embed_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    
    # 取得原始圖片清單 (只取 .jpg, 因為原圖我們預設已經是預覽 jpg)
    raw_files = sorted(list(RAW_DIR.glob("*.jpg")))[:MAX_SAMPLES]
    
    # 若有已存在的快取，可以載入以接續執行
    dataset_features = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "rb") as f:
            dataset_features = pickle.load(f)
        print(f"已讀取現有快取資料：{len(dataset_features)} 筆")

    print(f"準備處理 {len(raw_files)} 張圖片...")
    for raw_path in tqdm(raw_files, desc="處理圖片"):
        img_id = raw_path.name
        
        # 若已經處理過，跳過 (支援斷點續傳)
        if img_id in dataset_features:
            continue
            
        raw_b64 = encode_image(raw_path)
        if not raw_b64:
            continue
            
        # 1. 提取 F_context 文字與向量
        context_text = extract_image_context(vlm_model, raw_b64)
        f_context = embed_model.encode(context_text) # 384-dim

        # 存放這張圖片的專家資料
        expert_data = {}
        for expert_id, expert_dir in EXPERT_DIRS.items():
            expert_path = expert_dir / img_id
            if not expert_path.exists():
                continue
                
            # 2. 提取 目標直方圖 Target_Histogram
            target_hist = calculate_histogram(expert_path)
            
            # 3. 提取 F_text (模擬使用者 Prompt) 文字與向量
            expert_b64 = encode_image(expert_path)
            style_text = extract_style_prompt(vlm_model, expert_b64)
            f_text = embed_model.encode(style_text) # 384-dim
            
            expert_data[expert_id] = {
                "target_hist": target_hist,
                "style_text": style_text,
                "f_text": f_text
            }
            
        if not expert_data:
            continue
            
        # 儲存特徵
        dataset_features[img_id] = {
            "context_text": context_text,
            "f_context": f_context,
            "experts": expert_data
        }
        
        # 每處理一張就存檔一次，以防崩潰
        with open(OUTPUT_FILE, "wb") as f:
            pickle.dump(dataset_features, f)

    print(f"資料預處理完成！共儲存 {len(dataset_features)} 筆訓練資料特徵至 {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
