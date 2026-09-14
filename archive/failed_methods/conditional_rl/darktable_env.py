import gymnasium as gym
from gymnasium import spaces
import numpy as np
import cv2
import pickle
import subprocess
import tempfile
import sys
from pathlib import Path

# 將外層目錄加入 sys.path，以便引入您先前寫好的 param_covert.py
sys.path.append(str(Path(__file__).resolve().parent.parent / "model_v2"))
from param_covert import build_xmp_text, encode_exposure_params, encode_contrast_params, encode_colorbalancergb_params

class DarktableEnv(gym.Env):
    """
    自定義的 Darktable 修圖強化學習環境。
    - State (Observation): F_context (384) + F_text (384) + F_color (當前圖片色彩直方圖 512)
    - Action: 10 維連續向量 [-1, 1]，對應曝光、黑層、對比、色相、飽和度等修圖參數。
    - Reward: 負的巴氏距離 (當前直方圖 vs 目標直方圖) + 極端像素懲罰。
    """
    def __init__(self, data_dir="../data", features_path="dataset_features.pkl", darktable_cli="C:/Program Files/darktable/bin/darktable-cli.exe", env_id=0):
        super(DarktableEnv, self).__init__()
        
        self.data_dir = Path(data_dir).resolve()
        self.raw_dir = self.data_dir / "raw"
        self.darktable_cli = darktable_cli
        self.env_id = env_id
        
        # 載入預先計算好的特徵快取
        with open(features_path, "rb") as f:
            self.dataset_features = pickle.load(f)
            
        self.image_keys = list(self.dataset_features.keys())
        
        # 動作空間: 10 維數值，範圍限制在 [-1.0, 1.0]
        # 分別代表: [exposure, black_level, highlights, shadows, detail, shadow_hue, highlight_hue, saturation, vibrance, contrast]
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(10,), dtype=np.float32)
        
        # 觀察空間 (狀態): Context (384) + Text/Prompt (384) + Current_Color_Histogram (512) = 1280 維
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(1280,), dtype=np.float32)
        
        self.current_img_id = None
        self.current_target_hist = None
        self.current_f_context = None
        self.current_f_text = None
        
        # 用於存放臨時輸出的目錄，加入 env_id 避免多核心執行時檔案衝突 (Race Condition)
        self.temp_dir = Path(tempfile.gettempdir()) / f"darktable_rl_temp_{self.env_id}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
    def _calculate_histogram(self, image_path):
        """計算並回傳圖片的 512維 RGB直方圖"""
        img = cv2.imread(str(image_path))
        if img is None:
            return np.zeros(512, dtype=np.float32)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        hist = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        hist = cv2.normalize(hist, hist).flatten()
        return hist.astype(np.float32)
        
    def reset(self, seed=None, options=None):
        """回合初始化：隨機挑選一張圖與一個目標風格 (專家 C 或 E)"""
        super().reset(seed=seed)
        self.current_img_id = np.random.choice(self.image_keys)
        data = self.dataset_features[self.current_img_id]
        
        self.current_f_context = data["f_context"]
        
        # 隨機挑選專家 C 或 E 作為風格目標
        experts = list(data["experts"].keys())
        chosen_expert = np.random.choice(experts)
        expert_data = data["experts"][chosen_expert]
        
        self.current_f_text = expert_data["f_text"]
        self.current_target_hist = expert_data["target_hist"]
        
        # 初始狀態為原始圖片的直方圖
        raw_path = self.raw_dir / self.current_img_id
        current_hist = self._calculate_histogram(raw_path)
        
        # 拼接 State: [F_context, F_text, F_color]
        state = np.concatenate([self.current_f_context, self.current_f_text, current_hist])
        return state, {}

    def _map_action_to_params(self, action):
        """將 [-1, 1] 的動作映射到實際 Darktable 模組的合理參數範圍"""
        # action[0]: exposure (-3.0 to +3.0)
        exposure = action[0] * 3.0
        # action[1]: black_level (-0.1 to +0.1)
        black_level = action[1] * 0.1
        # action[2], action[3]: highlights, shadows (0.0 to 1.0)
        highlights = (action[2] + 1.0) / 2.0
        shadows = (action[3] + 1.0) / 2.0
        # action[4]: detail (0.0 to 1.0)
        detail = (action[4] + 1.0) / 2.0
        # action[5], action[6]: shadow_hue, highlight_hue (-0.5 to +0.5)
        shadow_hue = action[5] * 0.5
        highlight_hue = action[6] * 0.5
        # action[7], action[8], action[9]: sat, vib, contrast (-0.5 to +0.5)
        saturation = action[7] * 0.5
        vibrance = action[8] * 0.5
        contrast = action[9] * 0.5
        
        return {
            "exposure": exposure, "black_level": black_level,
            "highlights": highlights, "shadows": shadows, "detail": detail,
            "shadow_hue": shadow_hue, "highlight_hue": highlight_hue,
            "saturation": saturation, "vibrance": vibrance, "contrast": contrast
        }

    def step(self, action):
        """將 Agent 輸出的動作轉換為 XMP 並呼叫 Darktable 渲染"""
        # 1. 動作轉換
        params = self._map_action_to_params(action)
        
        # 2. 生成 XMP 歷史圖層 (利用 param_covert.py 的 Base64 編碼邏輯)
        # 此處展示了串接 exposure, bilat(對比), 與 colorbalancergb(色彩) 三個模組
        layers = []
        
        # Exposure
        layers.append({
            "op": "exposure", "ver": "7", 
            "params": encode_exposure_params(params["exposure"], params["black_level"])
        })
        # Local Contrast (bilat)
        layers.append({
            "op": "bilat", "ver": "3",
            "params": encode_contrast_params(params["highlights"], params["shadows"], params["detail"], 0.5)
        })
        # Color Balance RGB
        layers.append({
            "op": "colorbalancergb", "ver": "5",
            "params": encode_colorbalancergb_params(
                shadow_hue=params["shadow_hue"], highlight_hue=params["highlight_hue"],
                saturation=params["saturation"], vibrance=params["vibrance"], contrast=params["contrast"]
            )
        })
        
        # 3. 寫入 XMP 暫存檔
        xmp_str = build_xmp_text(layers, derived_from=self.current_img_id)
        xmp_path = self.temp_dir / f"{self.current_img_id}.xmp"
        with open(xmp_path, "w", encoding="utf-8") as f:
            f.write(xmp_str)
            
        # 4. 呼叫 Darktable-cli 渲染 (極重要：加入 --width 限制解析度以加速渲染)
        raw_path = self.raw_dir / self.current_img_id
        # 修正雙副檔名的問題 (如果 current_img_id 已經有 .jpg)
        img_stem = Path(self.current_img_id).stem
        out_img_path = self.temp_dir / f"out_{img_stem}.jpg"
        
        # 如果檔案存在先刪除
        if out_img_path.exists():
            out_img_path.unlink()
            
        cmd = [
            self.darktable_cli, raw_path.as_posix(), xmp_path.as_posix(), out_img_path.as_posix(),
            "--width", "360", "--height", "360", 
            "--hq", "false",    # 停用高品質重採樣與馬賽克處理，大幅加速渲染
            "--core", 
            "--configdir", self.temp_dir.as_posix(), # 獨立設定檔目錄，避免多核心同時寫入 SQLite 資料庫造成 Lock 崩潰
            "--conf", "plugins/imageio/format/jpeg/quality=85",
            "--conf", "write_sidecar_files=false"  # 禁止覆寫或產生多餘的 sidecar 避免鎖死
        ]
        
        try:
            # 擷取輸出以幫助我們 debug，放寬 timeout 時間避免 12 核互搶資源時超時
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                print(f"Darktable 執行失敗，錯誤代碼: {result.returncode}")
                print(f"Stderr: {result.stderr}")
        except subprocess.TimeoutExpired as e:
            print("Darktable rendering timed out!")
            if getattr(e, 'stderr', None):
                print(f"Timeout Stderr: {e.stderr}")
                
        # 確認檔案確實存在才進行讀取，否則給予懲罰或預設黑圖
        if not out_img_path.exists():
            print(f"[Warning] Darktable did not generate the output file: {out_img_path}")
            current_hist = np.zeros(512, dtype=np.float32)
            distance = 1.0 # 懲罰距離
        else:
            # 5. 獲取新圖片的直方圖特徵
            current_hist = self._calculate_histogram(out_img_path)
            # 6. 計算 Reward (利用巴氏距離 Bhattacharyya distance)
            distance = cv2.compareHist(current_hist, self.current_target_hist, cv2.HISTCMP_BHATTACHARYYA)
            
        reward = -float(distance)
        # 極端值懲罰 (如果動作全在邊界，給予一點懲罰)
        reward -= 0.1 * float(np.sum(np.abs(action) > 0.9))
        
        next_state = np.concatenate([self.current_f_context, self.current_f_text, current_hist])
        terminated = True # One-shot episode
        truncated = False
        info = {"distance": distance}
        
        # 由於多核心執行時 print 會造成畫面字元交錯亂碼，交給 SB3 的 logger 即可
        
        return next_state, reward, terminated, truncated, info
