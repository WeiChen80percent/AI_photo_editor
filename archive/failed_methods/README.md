# 未採用／失敗方法封存

這個目錄保存 AI Photo Editor 開發期間試過、但沒有進入目前 Web／Mobile 正式執行路徑的方法。

這裡的「失敗」表示未成為目前產品方案，可能原因包括效果、複雜度、外部工具依賴、訓練成本或已被其他方法取代；不表示每個程式都無法單獨執行。

GitHub 只保留這些方法的訓練／資料準備／推論程式、相依清單與說明文件。模型、checkpoint、NumPy／Pickle 資料、訓練圖片、資料庫、log 與 run output 不提交；原本已追蹤的二進位內容仍可從 Git 歷史找回。

Python `__pycache__`／`*.pyc` 與單次修圖輸出也不提交，因為它們不是方法原始碼。

| 封存目錄 | 原路徑 | 方法 | 未進入主線的原因 |
|---|---|---|---|
| `langchain_model/` | `/langchain_model/` | LangChain + Ollama + Darktable XMP | 早期原型；正式後端改用 FastAPI 語意層與 OpenCV |
| `model_v2/` | `/model_v2/` | Darktable PhotoAgent v2 + VLM | 需要 Darktable CLI，根目錄 README 已標示 Darktable 暫停 |
| `opencv_rl/` | `/opencv_rl/` | OpenCV 強化學習修圖 | 未被正式後端引用，已有大量 checkpoint／訓練輸出 |
| `conditional_rl/` | `/conditional_rl/` | Conditional RL | 未被正式後端引用，未成為目前雙模型方案 |
| `hybrid_parametric_retouch/` | `/hybrid_parametric_retouch/` | 混合參數式修圖模型 | 未被正式後端引用，只保留方法程式供研究追溯 |
| `histogram_global_3dlut/` | `/histogram_global_3dlut/` | Histogram Global 3D LUT | 未被正式後端選用；正式忠實模型使用 `image_adaptive_3dlut/` |
| `dataset_pilot/` | `/dataset_pilot/` | 反向劣化資料 pilot | 只是早期資料與 action-label 可行性驗證，不是產品執行元件 |
| `lightroom_preset/` | `/lightroom_preset/` | Lightroom preset 蒐集與資料庫 | 正式風格改用後端核准的 Style Catalog；原始素材另有授權與體積考量 |

## 目前正式保留的方法

- `/backend/`：FastAPI、OpenCV、語意解析、歷史、手動調整與 Photo Git。
- `/image_adaptive_3dlut/`：後端 `expert_faithful_lut` 自動模型直接載入。
- `/residual_fusion/`：後端 `vivid_residual_fusion` 自動模型直接載入。
- `/frontend/`：原網頁版。
- `/mobile_app/`：獨立手機 App。

若要重新實驗，請依各方法 README 安裝環境並重新準備資料、訓練模型。封存區不提供預訓練權重或訓練資料。`lightroom_preset/` 的既有素材雖可從 Git 歷史找回，重新發布前仍應確認來源授權。
