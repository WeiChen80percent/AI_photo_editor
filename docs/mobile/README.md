# AI Photo Editor 手機版 Spec 與實作紀錄

更新日期：2026-09-09

## 專案分工

| 目錄 | 用途 | 修改原則 |
|---|---|---|
| `frontend/` | 原本 Flutter 網頁版，以及原先共用的 Android／iOS 骨架 | 保持本次實作開始前的原樣；手機功能不得加在此處 |
| `mobile_app/` | 從原本 `frontend/` 複製出的獨立手機 App | 所有新手機介面、權限、相簿及工作恢復功能放在此處 |
| `backend/` | 兩個前端共用的既有 FastAPI 修圖後端 | 本次不改既有 API 行為；只新增手機展示啟動腳本與環境快照 |

開始時並不存在獨立的 `mobileapp/`。原本手機骨架包含在 `frontend/android` 與 `frontend/ios`，並和 Web 共用 `frontend/lib`。本次建立 `mobile_app/`，內容以當時的原本手機骨架和修圖功能為基礎，再加入手機功能。

「網頁版保持原樣」的檢查標準：`git status --short -- frontend` 與 `git diff --name-only -- frontend` 均無輸出。工作開始時缺少的三個 Flutter 多語系生成檔已從版本庫原始內容恢復，因為缺少它們會讓 Web 無法分析或建置；沒有更改網頁介面、功能、套件版本或平台設定。既有 `backend/app/routes/edit.py` 和 `backend/start_backend_llm.ps1` 也沒有差異。

## 第一版目標

- Android 優先，同一 Wi-Fi 連接電腦 FastAPI 後端，作為一週內的專題展示版。
- App 名稱暫定 `AI Photo Editor`，Android application ID 為 `tw.edu.gradproject.aiphotoeditor`。
- 保留原網頁版既有功能：文字／語音修圖、參考圖、雙模型比較、後端目前提供的 50 筆風格、15 個手動參數、局部修圖、前後比較、歷史分支、Photo Git、修圖限制條件、繁中／英文和深淺色。
- 輸入只支援 JPEG/JPG、PNG；不支援 HEIC、RAW。
- 匯出沿用後端正式結果，不重新縮放或壓縮；顯示實際像素尺寸。
- 不包含商店上架、正式簽章、帳號、雲端同步、完全離線模型、拍照、分享及批次修圖。

## 已實作功能

### MOB-01 後端連線

- `mobile_app` 頂部「更多操作」提供後端連線設定。
- 原生手機第一次以 loopback 位址啟動時，自動要求設定電腦位址。
- 接受完整 HTTP(S) URL，拒絕空主機、帳密、query、fragment 或非法連接埠。
- 「檢查連線」以 8 秒上限呼叫 `/health`，且必須收到 `status=good`。
- 本機保存的位址優先於 `--dart-define=API_BASE_URL=...`。
- Android 模擬器使用 `http://10.0.2.2:8000`；真機使用 `http://<電腦區網 IPv4>:8000`。
- Android 展示版允許區網 HTTP。公開網路部署仍應使用 HTTPS、認證與資料隔離。

### MOB-02 圖片匯入

- 在手機端檢查實際 JPEG／PNG 檔頭和可解碼尺寸，不只依賴副檔名。
- 上限 40 MiB、24,000,000 像素；不自動縮圖或轉碼。
- 成功選圖後顯示實際像素尺寸；取消或格式錯誤不清除目前工作。
- Android 啟動時呼叫 `retrieveLostData()`，並以選圖前保存的角色分辨原圖和參考圖。
- 若參考圖恢復時沒有可用原圖，要求重新選擇，不把參考圖誤當原圖。
- 未提交的原圖不做永久快取；在選圖期間重啟可能需要重新選原圖。

### MOB-03 相簿匯出

- 「更多操作 → 將選定版本儲存到相簿」。
- 只儲存已正式提交且目前選定的版本；有未套用手動／Photo Git 草稿時禁止匯出。
- 從該版本的 `resultUrl` 下載，不使用畫面截圖或暫時預覽。
- 下載上限 40 MiB、90 秒；完成後關閉連線並刪除本次唯一暫存副本。
- 使用 `gal` 寫入相簿，處理權限拒絕、空間不足及格式錯誤。
- 不重新壓縮，成功訊息顯示相簿檔案的實際像素尺寸。

### MOB-04 最近工作恢復

- 本機保存最近成功收到的 `server / session_id / selected_edit_id`。
- 重開 App 後從相同後端取得完整 session，可恢復原圖基準或指定正式版本。
- 指定版本不存在時退回該 session 最後一版。
- 斷網時保留書籤，讓使用者從「恢復最近工作」重試。
- 後端 session 已不存在時提示重新開始。
- 未提交的滑桿、Photo Git 計畫、文字草稿、參考圖與錄音不跨重啟保存。
- 書籤只有最近一份，不是多專案管理器。

### MOB-05 語音與生命週期

- 原生平台錄音前檢查麥克風權限；Web 原有錄音邏輯不在 `frontend/` 中更動。
- 保留 PCM16、16 kHz、單聲道 WAV 與 15 秒限制。
- App 進入背景或隱藏時取消錄音；權限彈窗期間不誤判為一般背景離開。
- 返回鍵若遇到錄音會先取消；手動／Photo Git 草稿會先詢問是否捨棄。

### MOB-06 網路與操作一致性

- 語音等待 75 秒、一般 GET 30 秒、修圖和雙模型完整回應 180 秒。
- 相簿下載使用獨立連線，逾時或大小超限會關閉傳輸。
- 手機恢復／匯出／處理期間鎖定可能替換工作區的操作，避免晚到回應覆蓋新工作。
- 更換後端時建立新的 controller/API 畫面，舊畫面的晚到回應不能寫入新工作區。
- 既有後端的去重、預覽、歷史和錯誤交易行為未修改；其限制應在測試報告列出。

## 平台與套件變更

只修改 `mobile_app/` 的平台檔：

- Android：INTERNET、RECORD_AUDIO、舊版相簿寫入權限、區網 HTTP、App 名稱、application ID、minSdk 24。
- iOS：相簿讀取／新增、麥克風、區網用途說明與 local networking 設定。iOS 尚未在 Mac/Xcode 建置。
- 新增 Flutter 套件：`gal 2.3.3`、`path_provider 2.1.6`。
- 沿用：`image_picker`、`http`、`record`、`shared_preferences`、Flutter 多語系。

Android release 暫時仍使用本機 debug key，只供展示安裝，不可當成商店發布版本。

## 開發環境與啟動

已偵測 Flutter 3.47.2、Dart 3.13.2、Android SDK API 36、Python 3.13.7。Android Studio 2026.1.4.7 隨附 OpenJDK 25.0.3，但目前 Gradle 8.14 不相容，因此 Flutter 已改用 Temurin JDK 17.0.20.1。Pixel 7／API 35 AVD `AI_Photo_Editor_API_35` 仍保留但禁止自動啟動。

開發與 Android 測試電腦需要安裝：

- Flutter SDK（內含 Dart）及加入 `PATH` 的 `flutter` 指令。
- Android Studio、Android SDK、Platform Tools、Build Tools、Command-line Tools，以及開啟 USB 偵錯的 Android 真機；本機建置使用 JDK 17。模擬器因主機異常禁止使用。
- Python 3.13 相容環境與 `backend/requirements-mobile.lock.txt` 所列後端套件；AI 模型仍依原專案設定使用 PyTorch、Whisper／Ollama 等既有服務。
- 若要測 iOS，另需 macOS、Xcode、CocoaPods、Apple 簽章設定和 iPhone／iOS Simulator；Windows 無法完成 iOS 原生建置。

Flutter 套件由 `mobile_app/pubspec.yaml` 管理，無須逐一手動全域安裝。新增的直接相依是 `gal 2.3.3` 與 `path_provider 2.1.6`；其餘沿用套件也會由 `flutter pub get` 解析。

準備手機專案，不建置 APK、不執行功能測試：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\prepare_mobile.ps1
```

此腳本只處理 `mobile_app/` 的 Flutter 套件、多語系和缺少的 Gradle wrapper，不會修改 `frontend/`。

啟動共用的原本後端：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\backend\start_backend_mobile.ps1
```

預設使用根目錄 `.venv`、`0.0.0.0:8000`、單 worker；可用 `-PythonPath`、`-Port`、`-DisableLlm` 調整。`backend/requirements-mobile.lock.txt` 是目前 Windows 手機展示後端的相依快照，不取代原本的 `backend/requirements.txt`。

### Web 與手機的啟動指令

兩個前端共用同一個 FastAPI 後端。先在終端 A 從專案根目錄啟動：

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject
powershell -NoProfile -ExecutionPolicy Bypass -File .\backend\start_backend_mobile.ps1
```

若要使用 Ollama 語意功能，另開終端執行 `ollama run llama3.2:3b`。後端健康檢查網址為 `http://127.0.0.1:8000/health`。

測 Web 時，在終端 B 執行：

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject\frontend
flutter run --no-pub -d chrome --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

`--no-pub` 用來避免測試時自動更新原網頁版的 lock file 或平台產生檔。若套件快取缺失，先記錄 `git status`，再執行 `flutter pub get`，完成後不得提交非預期的 Web 檔案變動。

測 Android 真機時，先用 `flutter devices` 取得 ID，再執行：

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\prepare_mobile.ps1
cd .\mobile_app
flutter run -d <裝置ID> --dart-define=API_BASE_URL=http://<電腦區網IPv4>:8000
```

真機和電腦必須在同一個 Wi-Fi。Android Emulator 原本應使用 `http://10.0.2.2:8000`，但本機目前禁止自動啟動模擬器，原因記錄如下。

### 模擬器系統異常紀錄

2026-09-09 首次啟動新 AVD 後，Windows 發生非正常關機。重新開機後確認沒有殘留 emulator／QEMU 程序。System Event Log 有 Kernel-Power 41 和 EventLog 6008；`BugcheckCode=0`、`WHEABootErrorCount=0`，也沒有 minidump 或 `MEMORY.DMP`，目前無法判定是 AEHD、GPU、電源或其他系統元件造成。

- 測試 Agent 不得自行啟動任何 Android Emulator，也不得變更 hypervisor、BIOS、Windows 虛擬化或 GPU 驅動。
- Android Studio、SDK、API 35 system image 和 AVD 保留，但在使用者明確同意重新嘗試前，只能進行唯讀檢查。
- 手機 App 的原生驗收改用實體 Android；若目前沒有裝置，相關項目標為受阻，不得推測通過。

### 後續共用程式碼整合

目前 Web 與手機已在同一個 Git 專案根目錄，分別為 `frontend/` 與 `mobile_app/`，並共用 `backend/`。這是目前最安全的雙版本結構。

等 T01～T12 驗收完成後，可以再把重複的 Dart 程式抽成同一個共用 package，但不建議直接把兩套平台設定混回單一 Flutter app。建議結構：

```text
apps/web/            # 由 frontend 搬入
apps/mobile/         # 由 mobile_app 搬入
packages/editor_shared/  # controller、models、API、共用 UI 與多語系
backend/
```

Web 與 Mobile 各自保留 `pubspec.yaml`、入口、平台權限及發行設定，再以 path dependency 引用 `editor_shared`。整合時每搬一組檔案都必須重新執行 Web 與 Mobile 回歸，且不得在尚未驗收前進行此重構。

GitHub 提交時只加入 `mobile_app/`、`docs/mobile/`、手機後端啟動檔與檢查工具。大型照片資料、測試輸出、訓練快取與失敗方法模型已由根目錄 `.gitignore` 排除。

## 已完成的無裝置測試

- `mobile_app` 的 Flutter 套件已解析，多語系檔已產生。
- Dart formatter 檢查完成；PowerShell 啟動腳本、Android XML、iOS plist 與三個 UTF-8 ARB 檔均可解析。
- `frontend` 與 `mobile_app` 分別執行 Dart analyzer，兩者皆為 `No issues found!`。
- `git status --short -- frontend` 及 `git diff --name-only -- frontend backend/app/routes/edit.py backend/start_backend_llm.ps1` 均無輸出。
- Flutter 使用 JDK 17；Android SDK API 36、Build Tools 35.0.0、NDK 28.2 與 CMake 3.22.1 可完成建置。
- Web release 建置成功；Mobile debug/release APK 均建置成功，且 APK v2 簽章驗證通過。Release 目前仍為 debug 憑證，只供測試展示。
- `mobile_app/test/mobile_foundation_test.dart` 共 11 項通過；`tools/check_mobile_parity.py` 驗證 8 個共用檔、14 個 API 方法、6 個工具、多語系與平台權限均相符。
- 共用後端的 health、JPEG 上傳、文字修圖、PNG 結果、session、手動預覽與提交已完成實際 smoke test。
- 後端 `/edit/styles` 實際回傳 50 筆；舊文件所寫 52 筆已依執行結果修正。Web 與 Mobile 共用同一端點，因此兩端數量一致。
- Android Emulator 啟動曾造成 Windows 非正常關機，本輪未啟動模擬器。

## 驗收狀態

完整證據與限制見 [TEST_REPORT.md](TEST_REPORT.md)。無實體裝置時，只能通過可由主機驗證的範圍：

| ID | 驗收範圍 | 狀態 |
|---|---|---|
| T01 | `frontend/` 無追蹤檔差異、靜態分析通過；Web 建置、啟動、畫面與功能回歸 | 部分通過：分析、release 建置與無差異通過；瀏覽器互動未測 |
| T02 | Flutter doctor、debug/release APK 建置、安裝、冷啟動 | 部分通過：兩種 APK 建置與簽章通過；安裝、冷啟動受阻 |
| T03 | 連線 URL、health、保存、斷網、切換 A/B 後端 | 部分通過：URL、health、保存及錯誤處理自動測試通過；真機斷網與 A/B 未測 |
| T04 | JPEG/PNG、偽副檔名、HEIC/RAW、損壞、40 MiB／24 MP 邊界 | 部分通過：JPEG/PNG、損壞、格式與 40 MiB 自動測試通過；24 MP 邊界待補 |
| T05 | EXIF 方向、透明 PNG、大圖記憶體、Android Activity 回收 | 未測 |
| T06 | 相簿權限、選定正式版本、禁止預覽誤存、尺寸與內容比對 | 部分通過：結果 URL 與真實後端 PNG 通過；相簿需真機 |
| T07 | 最近工作、原圖基準、404、斷網重試、清除與改後端 | 部分通過：本機保存與真實 session 通過；其餘需裝置互動 |
| T08 | 麥克風權限、15 秒、背景、鎖屏、取消與晚到回應 | 未測 |
| T09 | 原有修圖功能完整回歸與真實模型可用性 | 部分通過：靜態功能/API 對等與 OpenCV 實際流程通過；雙 AI 模型只驗證 ready 狀態 |
| T10 | 小螢幕、橫直向、鍵盤、大字體、安全區域、返回鍵與手勢 | 未測 |
| T11 | 12 MP／24 MP、冷暖模型、滑桿預覽和長時間操作效能 | 未測 |
| T12 | iOS 建置與真機 | 受阻：目前 Windows，尚無 Mac 資訊 |

完整新聊天指令見 [TEST_PROMPT.md](TEST_PROMPT.md)。測試報告應另寫 `docs/mobile/TEST_REPORT.md`，不要覆蓋本實作紀錄。

## 已知限制

1. AI 在電腦後端執行，App 不是離線軟體。
2. `/health` 成功不代表雙模型、Whisper、語意模型或 Ollama 已可推論。
3. 原圖尺寸優先保留，但需逐一比對模型輸出；不保證透明度、EXIF、GPS、ICC 等中繼資料保留。
4. 首次請求若後端完成，但 App 在收到 session ID 前被終止，本機沒有足夠資訊自動找回該 session。
5. 既有後端行為為了保持網頁版完全不變，本次沒有加入新的普通 `/edit` 去重或上傳格式限制。
6. Windows 防火牆、Wi-Fi 用戶隔離和真機 USB 驅動尚未設定或測試。
7. Android Emulator 目前列為主機穩定性風險；在原因排除前不可用它進行驗收。
