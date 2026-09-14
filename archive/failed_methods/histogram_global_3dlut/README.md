# Histogram-conditioned Global Residual 3D LUT (V2)

V2 是針對已刪除的 tone-region V1 所做的保守重設計。詳細失敗分析見
[V1_FINDINGS.md](V1_FINDINGS.md)。原始 `image_adaptive_3dlut` baseline 保持不變。

## 為什麼改成全域模型

V1 的三明度區 LUT 參數增加到 1,295,504，卻只有 0.038 dB PSNR 提升，且主觀效果
不如原版。V2 移除陰影／中間調／高光區域處理，只加入：

- RGB、亮度、飽和度共 80 維 soft histogram；
- 三個全域 LUT 權重 residual，範圍限制為正負 0.25；
- 一個全域 strength gate，0 可取消調色、1 等於 baseline、2 可加強；
- 低權重 SSIM/Lab loss，不再讓輔助 loss 主導 MSE。

總參數量 598,960，只比 baseline 的 593,516 增加 5,444。Histogram head 初始輸出為
零，因此載入 baseline 後，V2 在數值上精確重現 baseline。

## 訓練策略

1. Epoch 1–5：凍結 baseline，只訓練 histogram head。
2. Epoch 6 起：僅解凍 LUT 與 CNN 最後一層，早期 CNN 永久凍結。
3. 最多 40 epochs，validation 綜合分數連續 10 epochs 未改善便停止。
4. 同時保存 `best.pt`（綜合分數）和 `best_psnr.pt`。

## 測試程式

```powershell
uv run python -m unittest histogram_global_3dlut.test_global -v
uv run python -m histogram_global_3dlut.smoke_test
```

## 正式訓練

```powershell
uv run python -m histogram_global_3dlut.train
```

輸出位置：

```text
histogram_global_3dlut/runs/expert_c/
|-- checkpoints/best.pt
|-- checkpoints/best_psnr.pt
|-- checkpoints/latest.pt
|-- metrics.jsonl
`-- result.json
```

## 中斷續訓

```powershell
uv run python -m histogram_global_3dlut.train `
  --resume histogram_global_3dlut\runs\expert_c\checkpoints\latest.pt
```

## 公開 validation 評估

```powershell
uv run python -m histogram_global_3dlut.evaluate `
  --checkpoint histogram_global_3dlut\runs\expert_c\checkpoints\best.pt `
  --output-dir histogram_global_3dlut\runs\evaluation
```

## 與原版公平比較

```powershell
uv run python -m histogram_global_3dlut.compare_baseline `
  --candidate-checkpoint histogram_global_3dlut\runs\expert_c\checkpoints\best.pt `
  --output-dir histogram_global_3dlut\runs\baseline_vs_v2
```

會產生 `RAW | Baseline | Global Histogram | Expert C` 四欄圖片與指標差異。

## 單張推論

```powershell
uv run python -m histogram_global_3dlut.infer `
  --checkpoint histogram_global_3dlut\runs\expert_c\checkpoints\best.pt `
  --input "C:\Users\User\Pictures\test.jpg" `
  --output histogram_global_3dlut\runs\inference\test_enhanced.jpg
```

輸入不需要是 480p，推論只查看原圖，輸出維持原始解析度。

## 接受標準

只有同時滿足以下條件才視為真正進步：

- public development PSNR 至少提升 0.10 dB，或 Delta E 至少降低 0.15；
- 其他兩個客觀指標不得退步；
- 平均推論時間不超過 baseline 的 1.25 倍；
- 至少 30 張未見照片盲測，新版偏好率高於 55%。

未達標就保留原版作為正式模型，不以增加模組本身當作貢獻。

