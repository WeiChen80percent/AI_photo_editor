# OpenCV Teacher-Student RL Photo Editor

這個模組使用 OpenCV 的 20 個可解釋修圖參數，學習 Adobe FiveK
資料集中 Expert C 的調色方式。

最終 Student 模型在推論時只使用：

- 原始圖片
- 目前修圖結果
- 目前 slider 狀態與步數
- 從原圖擷取的 VGG16 特徵

推論時不會讀取 Expert C 圖片。Expert C 只會出現在 Teacher 訓練、
Teacher target 產生、Student PPO reward 與離線評估階段。

## 訓練流程

```text
Stage 1: Reference Teacher PPO
  Teacher 可看原圖、目前修圖狀態與 Expert C 特徵。
  目標是找出接近 Expert C 且不破壞細節的修圖參數。

Stage 2: Teacher Target Generation
  保存每張訓練圖在整條軌跡中的最佳 slider 組合。
  移除重複控制，最多保留 12 個主要參數。
  根據原圖與 Expert C 的差距建立 edit-strength 與 no-edit 標籤。

Stage 3: Validated Behavior Cloning
  Student 只看部署時可取得的原圖與修圖狀態。
  每張圖建立 10 個正常、偏離與修正中的狀態。
  使用 250 張 held-out 訓練圖做 BC early stopping，不回傳梯度。

Stage 4: No-Reference Student PPO
  Student 的 observation 不含 Expert C，但訓練 reward 可比較 Expert C。
  使用保守的 learning rate、clip range、KL 限制與驗證集選模。
  若連續兩次驗證沒有改善就提早停止，並恢復最佳 checkpoint。
```

Teacher、reward、checkpoint 選擇與測試統一使用同一套 Expert C 分數。
這個分數不只看像素 MSE，也包含低頻色調、LAB 色差、色相、彩度與
全域色彩統計。

## 修圖參數

```text
exposure, contrast, saturation, highlights, shadows, sharpness,
temperature, tint, vibrance,
orange_hue, orange_saturation,
green_hue, green_saturation,
blue_hue, blue_saturation,
black_point, white_point,
shadow_temperature, highlight_temperature, local_contrast
```

Teacher 可使用全部 20 個參數。交給 Student 的每張 target 最多保留
12 個主要參數，並處理下列容易重複的組合：

- saturation / vibrance
- contrast / local_contrast
- temperature / split tone
- exposure / black point / white point

Student policy 有 21 維輸出：前 20 維是參數增量，第 21 維是修圖強度
與停止門控。模型可對已經接近目標的照片不修或提早停止，並透過安全
檢查避免爆亮、死黑、剪裁與細節損壞。

## 安裝

使用 uv：

```powershell
uv sync
```

沒有使用 uv：

```powershell
python -m pip install -r opencv_rl\requirements.txt
```

資料結構：

```text
data/
  raw/
  c/
opencv_rl/
  train_keys.txt
  test_keys.txt
```

## 1. 預先計算 VGG

在專案根目錄執行：

```powershell
uv run python opencv_rl\precompute.py
```

如果完整 cache 已存在，程式會直接結束。需要重建時：

```powershell
uv run python opencv_rl\precompute.py --force
```

VGG 只從 `data/raw/` 擷取原圖特徵，不會讀取 Expert C。

## 2. 訓練前檢查

```powershell
uv run python opencv_rl\train_cv2.py --check-only
```

檢查內容：

- 4,500 張訓練圖與 500 張測試圖沒有重疊
- RAW、Expert C 與 VGG cache 完整
- 20 個零值修圖參數不會改變圖片
- reward 與離線評估使用相同 Expert C 分數
- Teacher observation 會受到 Expert C 影響
- Student observation 不會受到 Expert C 特徵改變影響

## 3. 從零完整訓練

```powershell
uv run python opencv_rl\train_cv2.py
```

預設設定：

- Teacher PPO：最多 750,000 timesteps
- Behavior cloning：最多 16 epochs，含 early stopping
- Student PPO：最多 750,000 timesteps，含 early stopping
- PPO environments：8
- Teacher action step：0.08，最多 20 步
- Student action step：0.10，最多 10 步
- Fit / validation：4,250 / 250
- 500 張 `test_keys.txt` 完全不參與訓練與選模
- PPO 預設使用 CPU，避免 SB3 MLP PPO 在 GPU 反而較慢

這個指令不依賴 v3、v4 或 v5 模型，會從零訓練 Teacher，再建立
Teacher targets、訓練 Student，最後輸出新的無參考模型。

若 Teacher 已存在、只想重新產生相容 target 並重練 Student：

```powershell
uv run python opencv_rl\train_cv2.py --reuse-teacher
```

只有明確使用 `--reuse-teacher` 才會重用 Teacher。舊 target 的版本或
資料 key 不相容時仍會自動重建。

常用參數：

```powershell
uv run python opencv_rl\train_cv2.py `
  --teacher-steps 750000 `
  --student-steps 750000 `
  --bc-epochs 16 `
  --num-envs 8 `
  --device cpu `
  --bc-device auto
```

訓練過程會更新目前模型的 history、checkpoint 與 training artifacts，
但不會修改歷史 v3、v4、v5 模型。

正式輸出：

```text
opencv_rl/final_cv2_rl_model.zip
opencv_rl/final_vec_normalize.pkl
opencv_rl/final_cv2_rl_model.metadata.json
```

程式只會在整個流程成功後，才用驗證最佳 Student 更新正式輸出。

## 4. 查看訓練曲線

```powershell
uv run python opencv_rl\plot_training_log.py
```

圖中會分開顯示：

- Teacher PPO reward 與驗證分數
- Behavior cloning 訓練 loss、驗證 MAE、方向與 gate 誤差
- Student PPO reward、無參考驗證分數、近目標退步率與遠目標改善率

較低的驗證 objective 較好，但也要搭配 `verify_multiple.py` 的實際圖片
判斷，不能只看 MSE 或 reward。

## 5. 測試集隨機比較

```powershell
uv run python opencv_rl\verify_multiple.py --count 10 --seed 42
```

模型會先在完全看不到 Expert C 的情況下完成修圖，之後程式才載入
Expert C 做評分與排版。輸出預設為：

```text
opencv_rl/multi_comparison.jpg
```

測試不同隨機照片時省略 `--seed`。

## 6. 修一張任意照片

使用訓練模型做 Expert C 調色：

```powershell
uv run python opencv_rl\edit_single_image.py `
  --input test.png `
  --prompt "把這張照片變好看"
```

直接調單一參數，不載入 RL 模型：

```powershell
uv run python opencv_rl\edit_single_image.py `
  --input test.png `
  --prompt "調亮一點"
```

先做模型調色，再追加 prompt：

```powershell
uv run python opencv_rl\edit_single_image.py `
  --input test.png `
  --prompt "專家調色並提高飽和度"
```

`--use-vgg` 是預設值。任意新圖片必須現場擷取一次 VGG16 特徵，這只是
一次前向推論，不是重新訓練 VGG。`--no-vgg` 雖然較快，但輸入分布與
訓練不同，結果不可作為正式效果比較。

## 7. 比較目前模型與歷史模型

```powershell
uv run python opencv_rl\compare_v3_v4_v5.py --count 6 --seed 42
```

這個程式保留研究比較用途。v3、v4、v5 是歷史 reference-conditioned
模型，在推論時會看到配對 Expert 圖；目前模型則完全看不到 Expert，
因此兩者不是相同難度，結果不能被描述成公平的部署比較。

需要的歷史檔案：

```text
final_cv2_rl_model_v3_20params.zip
final_vec_normalize_v3_20params.pkl
final_cv2_rl_model_v4_06_style_reward.zip
final_vec_normalize_v4_06_style_reward.pkl
final_cv2_rl_model_v5.zip
final_vec_normalize_v5.pkl
```

這些歷史模型不是執行 `train_cv2.py` 的必要條件。

## 重現性與限制

- 訓練、切分、BC sampling 與模型初始化使用固定 seed。
- 相同程式、資料、套件與硬體可得到相近結果，但浮點運算不保證逐位元相同。
- 無參考模型只能從訓練資料學習 Expert C 的一般規律，無法知道某一張
  測試圖真正配對的 Expert C 答案。
- OpenCV 的全域參數無法完整重現局部遮罩、曲線或人工選區；這是目前
  可達效果的主要上限。
