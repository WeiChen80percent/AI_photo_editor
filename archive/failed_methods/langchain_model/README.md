#  AI Image Editing Assistant (Darktable Engine)

這是一個基於 **LangChain** 與 **Ollama** 的智慧修圖助理。它能理解自然語言指令（例如：「讓照片更有電影感」或「把陰影調亮一點」），自動計算專業的影像參數，並透過 **Darktable-cli** 引擎生成最終成品。

##  功能

*   **自然語言修圖**：不需手動調整拉桿，直接透過對話修改照片。
*   **專業參數轉換**：內建二進位編碼器，可將人類直覺參數轉換為 Darktable 專用的 XMP 標籤格式。
*   **LLM 代理執行**：使用 `Llama 3.2` 模型進行意圖分析，精準調用 `exposure`、`local contrast` 與 `color balance` 等工具。
*   **自動化流程**：從指令接收到 XMP 生成，再到最終 `darktable-cli` 匯出，一氣呵成。

---

##  安裝套件及軟體
### 1. 安裝 Ollama (本地 LLM 引擎)
本專案依賴 Ollama 執行 Llama 3.2 模型：
1.  前往 **[Ollama 官網](https://ollama.com/)** 下載適合你作業系統的安裝檔 (Windows/macOS/Linux)。
2.  安裝完成後，開啟終端機 (Terminal/PowerShell) 並執行以下指令下載模型：
    ```bash
    ollama run llama3.2:3b
    ```
3.  確保 Ollama 正在後台執行（通常安裝後會自動啟動）。

### 2. 安裝 Darktable
*   前往 **[Darktable 官網](https://www.darktable.org/install/)** 下載並安裝。
*   **注意**：本專案預設的 Windows 調用路徑為 `C:/Program Files/darktable/bin/darktable-cli.exe`。若安裝位置不同，請至 `tool.py` 修改。

### 3. 安裝 Python 套件
請確保你的環境中安裝了以下依賴：
```bash
pip install langchain langchain-ollama
```
> **註**：`struct`, `base64`, `zlib`, `subprocess` 為 Python 內建函式庫，無需額外安裝。


---

## 📂 專案結構

*   `main.py`: 程式入口，負責初始化 Agent 與處理使用者輸入。
*   `tool.py`: 定義 LangChain Tools，包含曝光、對比度、色彩平衡等調整邏輯。
*   `param_covert.py`: 核心算法模組，負責將浮點數參數包裝成 Darktable 識別的十六進位或 Base64 格式。

---

## 🚀 使用方式

1.  確保你的專案目錄下有一張測試圖片（預設檔案名為 `sample1.jpg`）。
2.  執行主程式：
    ```bash
    python main.py
    ```
3.  在提示字元下輸入指令，例如：
    > 「讓這張照片看起來更溫暖，並且增加一點電影感的青藍色陰影。」
4.  程式將自動生成 `sample1.jpg.xmp` 並調用 Darktable 引擎匯出結果。

---

## 📝 待辦事項 (To-do List)

- [ ] **新增更多修圖工具**
    - 曲線調整 (Tone Curve)
    - 銳化與降噪 (Sharpen / Denoiser)
    - 遮罩支持 (Masking support)
- [ ] **新增 CLIP 描述圖片**
    - 整合 CLIP 或 Llava 模型，在修圖前自動分析圖片內容。
    - 讓 Agent 能夠根據「圖片內容」給出更精確的建議（例如：檢測到人臉時自動優化膚色）。

---

## ⚠️ 注意事項

*   **路徑設定**：若你的 Darktable 安裝路徑不同，請修改 `tool.py` 中 `apply_xmp_and_export` 函式內的 `cmd` 路徑。
*   **效能**：本地運作 Llama 3.2 視硬體配置可能會有數秒的推論延遲。
