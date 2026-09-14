# Conditional RL 修圖系統開發計畫

## 專案結構與目標

此資料夾 (`conditional_rl/`) 包含將端到端條件式強化學習（Goal-Conditioned RL）整合至 Darktable 修圖系統的核心實作。

### 核心階段
- **階段一 (`dataset_prep.py`)**: 離線資料預處理與特徵快取。包含 VLM 分析原圖 ($\mathcal{F}_{context}$)、VLM 比對原圖與專家圖生成風格描述 ($\mathcal{F}_{text}$)、以及色彩直方圖萃取 ($\mathcal{H}_{target}$)。
- **階段二 (`darktable_env.py`)**: OpenAI Gymnasium 環境封裝，連接策略網路與 `darktable-cli`，並以直方圖巴氏距離作為主要 Reward 來源，加入極端像素的懲罰項。
- **階段三 (`models.py`, `train.py`)**: PPO / SAC Actor-Critic 網路，使用自定義特徵融合層（Feature Extractor）拼接上述三種特徵。
- **階段四 (`inference.py`)**: 部署用推理管線，能夠直接承接 FastAPI 後端的 request，快速單向傳播並匯出圖片。

## 執行細節與優化
1. **Gym 環境縮圖優化**: 在 `darktable_env.py` 呼叫 CLI 時會加入 `--width 360` 參數，將渲染時間從 2 秒壓縮至 0.05 秒。
2. **多模態特徵預處理**: 為了解決 RL 探索時的高延遲，預先用 VLM 及 Embedding 提取特徵，讓 Agent 專心於連續動作空間的探索。