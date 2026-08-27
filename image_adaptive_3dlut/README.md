# Image-Adaptive 3D LUT 影像自適應調色

本模組是 [Learning Image-adaptive 3D Lookup Tables for High Performance Photo Enhancement in Real-time](https://github.com/HuiZeng/Image-Adaptive-3DLUT) 的現代 PyTorch 實作。模型以輸入影像的內容預測三個基底 3D LUT 的組合權重，再用融合後的 LUT 對整張影像做可微分三線性插值，適合即時照片增強。

本專案使用 MIT-Adobe FiveK 的 sRGB 原圖與 Expert C 成品進行成對監督學習。資料集、訓練權重與產出影像不包含在 GitHub；模型權重另外發布於 Hugging Face。

## 功能

- 三個 `33 × 33 × 33` 可學習基底 LUT，以及依影像內容預測權重的小型 CNN。
- 純 PyTorch、支援 CPU/CUDA 的可微分三線性插值，不需要編譯舊版自訂 CUDA extension。
- 成對資料增強、訓練續跑、原子化 checkpoint 寫入與最佳模型選擇。
- 單張推論，以及 PSNR、SSIM、CIE Lab Delta E 評估。
- 可輸出原圖／預測／目標比較圖與每張影像的 JSON 結果。
- 提供論文結構測試與端到端 smoke test。

## 我的實作貢獻

本實作保留論文與官方 paired-training code 的模型架構、初始化方式、損失函數、正則化權重、資料增強範圍與預設訓練超參數，主要完成以下工程化與現代化工作：

1. 以 `torch.nn.functional.grid_sample` 重寫三線性 LUT 插值，移除過時的自訂 CUDA 相依性，同時保留可微分訓練能力。
2. 建立 manifest-based 成對資料載入器，處理 EXIF 方向、RGB 轉換、尺寸對齊、480p 前處理與可重現資料增強。
3. 加入資料洩漏防護：public validation 與 hidden evaluation 分離，hidden set 不能用來選 checkpoint。
4. 完成可續跑訓練流程，包括 random state、optimizer、epoch、global step、最佳模型與週期性 checkpoint。
5. 實作 PSNR、SSIM、CIE Lab Delta E、逐圖結果、彙總報告與視覺比較圖。
6. 提供單張影像 CLI 推論、官方權重轉換工具、單元測試及端到端 smoke test。
7. 在本地完成 400 epochs 訓練；`public_dev` 最佳 PSNR 為 `22.3318 dB`。此數值只代表目前資料切分與設定，不應直接與不同前處理或切分的結果比較。

第三方來源與授權說明請見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 運作原理

一般 3D LUT 以像素 `(R, G, B)` 作為色彩立方體座標，透過鄰近八個格點的三線性插值取得 `(R', G', B')`。本模型不是對所有照片套用固定 LUT，而是先由 CNN 對每張影像預測三個權重：

```text
輸入影像 ──► CNN ──► (w1, w2, w3)
    │
    └──► L = w1·LUT1 + w2·LUT2 + w3·LUT3
                         │
                         └──► 三線性插值 ──► 增強影像
```

第一個 LUT 初始化為 identity，另外兩個初始化為零；CNN 最後一層 bias 初始化為 1，因此模型初始輸出接近原圖。總損失為：

```text
L_total = L_MSE + 1e-4 · L_smooth + 10 · L_monotonicity
```

## 環境需求

- Python 3.10 以上；本專案實際訓練環境為 Python 3.13。
- PyTorch 2.4 以上與相容的 torchvision。
- NVIDIA GPU 為選用；CPU 可執行測試、評估與推論，但訓練時間會明顯增加。

請在包含 `image_adaptive_3dlut/` 的專案根目錄執行以下指令。

### 使用 pip

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r image_adaptive_3dlut\requirements.txt
```

Linux/macOS 的啟用指令為：

```bash
source .venv/bin/activate
```

若需要特定 CUDA 版本，請依 [PyTorch 官方安裝頁](https://pytorch.org/get-started/locally/) 安裝對應的 `torch` 與 `torchvision`。

### 使用既有 uv 專案

若上層專案已有 `pyproject.toml` 與 `uv.lock`：

```powershell
uv sync
```

後續將 `python` 換成 `uv run python` 即可。

## 下載 Hugging Face 模型

模型檔不放在 GitHub，已發布於 [WeiChen80percent/image-adaptive-3dlut](https://huggingface.co/WeiChen80percent/image-adaptive-3dlut)。先安裝 Hugging Face CLI，再下載最佳 checkpoint：

```powershell
python -m pip install huggingface_hub
hf download WeiChen80percent/image-adaptive-3dlut best.pt `
  --local-dir image_adaptive_3dlut\trained_model
```

若模型庫為 private，請先執行 `hf auth login`。下載完成後應有：

```text
image_adaptive_3dlut/trained_model/best.pt
```

`trained_model/` 與常見權重格式已列入 `.gitignore`，不會被加入 GitHub。建議 Hugging Face 模型庫至少包含 `best.pt`、訓練設定、指標、模型用途、資料來源、限制與授權資訊。

## 快速推論

```powershell
python -m image_adaptive_3dlut.infer `
  --checkpoint image_adaptive_3dlut\trained_model\best.pt `
  --input path\to\input.jpg `
  --output image_adaptive_3dlut\runs\inference\input_enhanced.jpg
```

沒有 CUDA 或需要強制使用 CPU 時，加上 `--cpu`：

```powershell
python -m image_adaptive_3dlut.infer `
  --checkpoint image_adaptive_3dlut\trained_model\best.pt `
  --input path\to\input.jpg `
  --output image_adaptive_3dlut\runs\inference\input_enhanced.jpg `
  --cpu
```

輸出包含增強後的影像，以及同名的 `.jpg.json` metadata；JSON 記錄模型路徑、checkpoint epoch、原始尺寸與三個 LUT 權重。

## 準備資料

本專案不散布 MIT-Adobe FiveK 或本地 manifests。請依資料集授權自行取得資料，建議目錄如下：

```text
data/
├── raw/
│   └── a0002-dgw_005.jpg
└── c/
    └── a0002-dgw_005.jpg
```

manifest 使用 JSON Lines，每行一組 input/target：

```json
{"id":"a0002-dgw_005.jpg","raw":"raw/a0002-dgw_005.jpg","target":"c/a0002-dgw_005.jpg"}
```

建立自己的 `train.jsonl` 與 `public_dev.jsonl` 後，可先產生 480p 快取以降低每個 epoch 的 JPEG 解碼與 resize 成本：

```powershell
python -m image_adaptive_3dlut.prepare_480p `
  --manifest path\to\train.jsonl `
  --manifest path\to\public_dev.jsonl `
  --data-root data `
  --output-root image_adaptive_3dlut\data_480p
```

資料載入流程會處理 EXIF 方向、轉換 RGB、檢查 input/target 長寬比差異不超過 5%，再將 target 對齊 input。訓練模式另會同步裁切與翻轉影像，並只對 input 做亮度、飽和度擾動。

## 訓練

論文設定的完整訓練範例：

```powershell
python -m image_adaptive_3dlut.train `
  --train-manifest path\to\train.jsonl `
  --val-manifest path\to\public_dev.jsonl `
  --data-root image_adaptive_3dlut\data_480p `
  --output-dir image_adaptive_3dlut\runs\paper_expert_c `
  --epochs 400
```

主要預設值：

| 參數 | 預設值 | 說明 |
| --- | ---: | --- |
| `--batch-size` | `1` | 論文設定；程式會拒絕其他值 |
| `--learning-rate` | `1e-4` | Adam learning rate |
| `--beta1`, `--beta2` | `0.9`, `0.999` | Adam 參數 |
| `--lambda-smooth` | `1e-4` | LUT smoothness 與 weight norm 權重 |
| `--lambda-monotonicity` | `10` | monotonicity 權重 |
| `--short-side` | `480` | 論文設定；程式會拒絕其他值 |
| `--eval-every` | `1` | 每幾個 epoch 評估一次 |
| `--save-every` | `10` | 每幾個 epoch 保留歷史 checkpoint |
| `--seed` | `42` | 隨機種子 |
| `--cpu` | 關閉 | 強制使用 CPU |

先用一筆資料驗證完整管線：

```powershell
python -m image_adaptive_3dlut.train `
  --train-manifest path\to\train.jsonl `
  --val-manifest path\to\public_dev.jsonl `
  --data-root image_adaptive_3dlut\data_480p `
  --output-dir image_adaptive_3dlut\runs\one_step `
  --train-limit 1 `
  --val-limit 1 `
  --epochs 1 `
  --max-steps 1 `
  --num-workers 0
```

從 `latest.pt` 續跑：

```powershell
python -m image_adaptive_3dlut.train `
  --resume image_adaptive_3dlut\runs\paper_expert_c\checkpoints\latest.pt `
  --train-manifest path\to\train.jsonl `
  --val-manifest path\to\public_dev.jsonl `
  --data-root image_adaptive_3dlut\data_480p `
  --output-dir image_adaptive_3dlut\runs\paper_expert_c `
  --epochs 400
```

訓練輸出包含 `best.pt`、`latest.pt`、週期性 checkpoint、preview、`metrics.jsonl`、`run_config.json` 與 `result.json`。這些檔案全部只保留於本機或模型託管平台，不提交 GitHub。

## 評估

```powershell
python -m image_adaptive_3dlut.evaluate `
  --checkpoint image_adaptive_3dlut\trained_model\best.pt `
  --manifest path\to\public_dev.jsonl `
  --data-root data `
  --output-dir image_adaptive_3dlut\runs\eval_public_dev
```

輸出包括：

- `summary.json`：平均 PSNR、SSIM、Delta E。
- `per_image.jsonl`：逐圖指標與 LUT 權重。
- `comparisons/`：原圖、模型預測、Expert C 的並排圖。

預設以 480p 評估；需要原始解析度可加 `--resolution original`。`--limit N` 可限制筆數，`--comparison-count N` 可調整比較圖數量。

hidden set 只能在模型與 checkpoint 已凍結後做最終測試，且必須明確加上 `--allow-hidden`：

```powershell
python -m image_adaptive_3dlut.evaluate `
  --checkpoint image_adaptive_3dlut\trained_model\best.pt `
  --manifest path\to\hidden_inputs.jsonl `
  --target-manifest path\to\hidden_targets.jsonl `
  --data-root data `
  --output-dir image_adaptive_3dlut\runs\eval_hidden `
  --allow-hidden
```

## 測試

```powershell
python -m unittest image_adaptive_3dlut.test_paper -v
python -m image_adaptive_3dlut.smoke_test
```

第一個指令驗證 identity LUT、插值、模型結構、正則化、metrics 與資料對齊；第二個指令實際執行一次 forward、loss、backward 和 optimizer step。CPU smoke test 可加 `--cpu`。

## 其他工具

將既有推論影像排成比較圖：

```powershell
python -m image_adaptive_3dlut.make_inference_comparisons `
  --inference-dir image_adaptive_3dlut\runs\inference
```

`--delete-singles` 會刪除原本的單張 enhanced 圖，具有破壞性，使用前請先確認備份。

轉換官方 `LUTs.pth` 與 `classifier.pth`：

```powershell
python -m image_adaptive_3dlut.convert_official `
  --luts path\to\LUTs.pth `
  --classifier path\to\classifier.pth `
  --output image_adaptive_3dlut\runs\official_srgb.pt
```

## 程式結構

```text
image_adaptive_3dlut/
├── model.py                       # LUT、CNN、融合與三線性插值
├── losses.py                      # MSE、smoothness、monotonicity
├── data.py                        # manifest 與 paired dataset
├── train.py                       # 訓練、驗證、checkpoint
├── infer.py                       # 單張推論
├── evaluate.py                    # 定量評估與比較圖
├── metrics.py                     # PSNR、SSIM、Delta E
├── prepare_480p.py                # 480p 資料快取
├── convert_official.py            # 官方權重轉換
├── checkpoints.py                 # 安全寫入與讀取 checkpoint
├── make_inference_comparisons.py  # 推論比較圖
├── test_paper.py                  # 單元測試
├── smoke_test.py                  # 端到端 smoke test
├── requirements.txt               # 最小執行相依套件
└── THIRD_PARTY_NOTICES.md          # 第三方來源與授權
```

以下內容刻意不放入 GitHub：

- `trained_model/`、`*.pt`、`*.pth`、`*.ckpt`、`*.safetensors`：改放 Hugging Face。
- `data/`、`data_480p/`、`manifests/`：本地資料與切分資訊。
- `runs/`、`outputs/`：訓練、評估與推論產物。
- `docs/*.pdf`：本地參考論文。
- `__pycache__/`、虛擬環境與工具快取。

## 引用與授權

若此實作對研究有幫助，請引用原論文：

```bibtex
@article{zeng2020learning,
  title={Learning Image-adaptive 3D Lookup Tables for High Performance Photo Enhancement in Real-time},
  author={Zeng, Hui and Cai, Jianrui and Li, Lida and Cao, Zisheng and Zhang, Lei},
  journal={IEEE Transactions on Pattern Analysis and Machine Intelligence},
  year={2020}
}
```

原始官方實作採 Apache License 2.0；本模組的來源與改動範圍詳列於 `THIRD_PARTY_NOTICES.md`。使用 MIT-Adobe FiveK 資料與發布模型時，仍需自行確認並遵守其資料集與權重授權條款。
