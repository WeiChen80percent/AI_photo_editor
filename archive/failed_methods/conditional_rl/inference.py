import torch
import numpy as np
import cv2
import base64
import sys
import subprocess
import json
import shutil
from pathlib import Path
from stable_baselines3 import PPO
from sentence_transformers import SentenceTransformer
from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

# 將外層目錄加入 sys.path，以便引入 param_covert
sys.path.append(str(Path(__file__).resolve().parent.parent / "model_v2"))
from param_covert import build_xmp_text, encode_exposure_params, encode_contrast_params, encode_colorbalancergb_params

# 從 models 匯入自定義特徵提取器
from models import MultiModalFeatureExtractor

class PhotoAgentInference:
    """
    此類別用於線上推論，支援提取特徵與調用 Darktable。
    """
    def __init__(self, model_path="final_conditional_rl_model.zip", darktable_cli="C:/Program Files/darktable/bin/darktable-cli.exe"):
        print("載入 RL 模型...")
        self.rl_model = PPO.load(model_path)
        
        print("載入 VLM 與 Embedding 模型...")
        self.vlm_model = ChatOllama(model="moondream:latest", temperature=0.1)
        self.embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        self.darktable_cli = darktable_cli
        
    def _encode_image(self, img_path):
        img = cv2.imread(str(img_path))
        if img is None: return None
        h, w = img.shape[:2]
        max_side = 512
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        _, buffer = cv2.imencode('.jpg', img)
        return base64.b64encode(buffer).decode('utf-8')

    def _calculate_histogram(self, img_path):
        img = cv2.imread(str(img_path))
        if img is None: return np.zeros(512, dtype=np.float32)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        hist = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        hist = cv2.normalize(hist, hist).flatten()
        return hist.astype(np.float32)

    def _map_action_to_params(self, action):
        """與 env 一致的對應關係"""
        # === 加入「安全輔助輪」===
        # 因為 50000 步的 RL 模型尚未完全收斂，為了保證 Demo 效果不翻車，
        # 我們將模型輸出的影響力 (Multiplier) 大幅限縮，確保它只會做「微調」。
        exposure = action[0] * 0.3           # 曝光最多只調加減 0.3 (原本 3.0)
        black_level = action[1] * 0.02       # 黑層微調
        highlights = 0.5 + (action[2] * 0.25) # 亮部基準 0.5，微調加減 0.25
        shadows = 0.5 + (action[3] * 0.25)    # 暗部基準 0.5，微調加減 0.25
        detail = 0.25 + (action[4] * 0.15)    # 細節基準 0.25，微調加減 0.15
        shadow_hue = action[5] * 0.1         # 色相微調
        highlight_hue = action[6] * 0.1      
        saturation = action[7] * 0.15        # 飽和度微調
        vibrance = action[8] * 0.15          # 鮮豔度微調
        contrast = action[9] * 0.15          # 對比度微調
        
        return {
            "exposure": exposure, "black_level": black_level,
            "highlights": highlights, "shadows": shadows, "detail": detail,
            "shadow_hue": shadow_hue, "highlight_hue": highlight_hue,
            "saturation": saturation, "vibrance": vibrance, "contrast": contrast
        }

    def extract_context(self, raw_image_path):
        """提取原始圖片的語意特徵"""
        b64_img = self._encode_image(raw_image_path)
        context_msg = "Briefly describe the exposure, lighting, and color balance of this photo in one short sentence."
        response = self.vlm_model.invoke([
            HumanMessage(content=[
                {"type": "text", "text": context_msg},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
            ])
        ])
        context_text = response.content.strip()
        return self.embed_model.encode(context_text)

    def predict_params(self, f_context, current_image_path, user_prompt):
        """根據目前的圖片狀態與使用者 Prompt 預測新的修圖參數"""
        f_color = self._calculate_histogram(current_image_path)
        f_text = self.embed_model.encode(user_prompt)
        
        obs = np.concatenate([f_context, f_text, f_color]).astype(np.float32)
        action, _states = self.rl_model.predict(obs, deterministic=True)
        return self._map_action_to_params(action)
        
    def render_image(self, raw_image_path, params, output_image_path):
        """產生 XMP 並呼叫 Darktable 渲染圖片"""
        layers = [
            {"op": "exposure", "ver": "7", "params": encode_exposure_params(params["exposure"], params["black_level"])},
            {"op": "bilat", "ver": "3", "params": encode_contrast_params(params["highlights"], params["shadows"], params["detail"], 0.5)},
            {"op": "colorbalancergb", "ver": "5", "params": encode_colorbalancergb_params(
                shadow_hue=params["shadow_hue"], highlight_hue=params["highlight_hue"],
                saturation=params["saturation"], vibrance=params["vibrance"], contrast=params["contrast"]
            )}
        ]
        
        xmp_str = build_xmp_text(layers, derived_from=Path(raw_image_path).name)
        xmp_path = Path(output_image_path).with_suffix('.xmp')
        with open(xmp_path, "w", encoding="utf-8") as f:
            f.write(xmp_str)
            
        cmd = [self.darktable_cli, Path(raw_image_path).as_posix(), Path(xmp_path).as_posix(), Path(output_image_path).as_posix(), "--core", "--conf", "plugins/imageio/format/jpeg/quality=95"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_image_path


class EditSessionManager:
    """
    編輯會話管理器，負責維護歷史紀錄資料夾，確保只保留最後一張實體圖片，並支援回溯功能。
    """
    def __init__(self, raw_image_path, agent: PhotoAgentInference, history_dir="./history"):
        self.raw_image_path = Path(raw_image_path)
        self.agent = agent
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        # 清理舊的 Checkpoint 暫存檔
        for p in self.history_dir.glob("checkpoint_*"):
            p.unlink()
            
        # 永遠只更新這張圖片
        self.current_output = self.history_dir / "current_output.jpg"
        
        print(f"初始化會話... 正在提取原圖 {self.raw_image_path.name} 的語意特徵...")
        self.f_context = self.agent.extract_context(self.raw_image_path)
        
        # 初始狀態：將原圖複製一份作為目前狀態
        shutil.copy(self.raw_image_path, self.current_output)
        
        # 歷史紀錄列表：儲存每次的參數與 checkpoint 路徑
        self.history = [] 
        self.step_counter = 0
        
    def apply_edit(self, user_prompt):
        """根據使用者指令套用修圖，從最後一張圖片狀態出發推論新參數"""
        print(f"\n[Step {self.step_counter + 1}] 收到修圖指令: {user_prompt}")
        print("AI 推論最佳參數中...")
        
        # 推論時餵入 '最後一張修圖結果' 的色彩直方圖
        params = self.agent.predict_params(self.f_context, self.current_output, user_prompt)
        print("模型輸出的修圖參數:", params)
        
        self.step_counter += 1
        ckpt_xmp = self.history_dir / f"checkpoint_{self.step_counter}.xmp"
        
        print("呼叫 Darktable 渲染最新圖片...")
        # 渲染會覆寫 current_output.jpg
        self.agent.render_image(self.raw_image_path, params, self.current_output)
        
        # 備份這次的 XMP 作為 Checkpoint
        current_xmp = self.current_output.with_suffix('.xmp')
        shutil.copy(current_xmp, ckpt_xmp)
        
        # 紀錄歷史
        self.history.append({
            "step": self.step_counter,
            "prompt": user_prompt,
            "params": params,
            "xmp_path": ckpt_xmp
        })
        print(f"修圖完成！圖片已更新至: {self.current_output}")
        return self.current_output
        
    def undo(self):
        """回溯到上一個修圖步驟"""
        if not self.history:
            print("\n目前沒有任何修圖紀錄可以回溯。")
            return self.current_output
            
        print("\n執行 Undo：正在回溯上一步...")
        popped_state = self.history.pop() # 移除最後一步
        
        if not self.history:
            # 如果回溯後沒紀錄了，回到原圖狀態
            shutil.copy(self.raw_image_path, self.current_output)
            print("已回溯至最原始的未修圖狀態。")
        else:
            # 回到上一個有效的 Checkpoint
            last_state = self.history[-1]
            print(f"正在恢復步驟 {last_state['step']} (指令: {last_state['prompt']}) 的狀態...")
            self.agent.render_image(self.raw_image_path, last_state["params"], self.current_output)
            print("回溯完成。")
            
        return self.current_output

if __name__ == "__main__":
    import random
    
    try:
        print("載入模型中...")
        agent = PhotoAgentInference(model_path="final_conditional_rl_model.zip")
        
        # 取得所有 RAW 圖片
        raw_dir = Path("../data/raw")
        all_images = list(raw_dir.glob("*.jpg"))
        if not all_images:
            raise FileNotFoundError(f"找不到任何圖片於 {raw_dir}")
            
        # 隨機挑選一張圖片
        random_image = random.choice(all_images)
        print(f"\n隨機挑選的測試圖片: {random_image.name}")
        
        session = EditSessionManager(
            raw_image_path=str(random_image),
            agent=agent,
            history_dir="./history"
        )
        
        print("\n=== 互動式修圖測試啟動 ===")
        print("提示：您可以輸入任何英文修圖指令，例如 'Make it dark and cinematic'")
        print("輸入 'undo' 可以回溯到上一步，輸入 'q' 或是 'exit' 可以離開程式。")
        
        while True:
            user_prompt = input("\n請輸入修圖指令: ").strip()
            
            if user_prompt.lower() in ['q', 'exit', 'quit']:
                print("結束測試！您可以到 ./history 資料夾查看最終成果。")
                break
            elif user_prompt.lower() == 'undo':
                session.undo()
            elif user_prompt == "":
                continue
            else:
                session.apply_edit(user_prompt)
                print("-> 渲染完成！請至 ./history/current_output.jpg 查看目前效果。")
        
    except Exception as e:
        print(f"發生錯誤: {e}")
        print("請確保路徑正確，或確認 Ollama 已啟動且 final_conditional_rl_model.zip 存在。")
