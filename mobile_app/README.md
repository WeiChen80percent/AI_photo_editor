# AI Photo Editor Mobile

這是從既有網頁版獨立出來的 Flutter 手機 App。原網頁版位於 `../frontend/`；手機功能只在 `mobile_app/` 修改。

完整規格與驗收狀態見 [`../docs/mobile/README.md`](../docs/mobile/README.md)，無裝置測試結果見 [`../docs/mobile/TEST_REPORT.md`](../docs/mobile/TEST_REPORT.md)。

## 使用 Android 實體手機執行

本機的 Android Emulator 曾造成 Windows 非正常關機，請勿啟動模擬器。以下流程使用 Android 7.0（API 24）以上實體手機。

### 1. 第一次安裝後端環境

在 PowerShell 進入專案根目錄。如果 `.venv` 已存在，可以跳過這一步。

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-mobile.lock.txt
```

### 2. 準備 Flutter 套件

在專案根目錄執行一次：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\prepare_mobile.ps1
```

這會在 `mobile_app/` 執行 `flutter pub get`、產生多語系檔案，並補齊 Android Gradle wrapper；不會啟動模擬器。

### 3. 找出電腦的區網 IP

手機與電腦連上同一個 Wi-Fi，再執行：

```powershell
ipconfig
```

在目前使用的 Wi-Fi 網卡下找到 `IPv4 Address`，例如 `192.168.1.25`。不要使用 `127.0.0.1`。若 Windows 防火牆詢問，僅允許目前信任的私人網路。

### 4. 啟動後端

開啟終端 A，在專案根目錄執行：

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject
powershell -NoProfile -ExecutionPolicy Bypass -File .\backend\start_backend_mobile.ps1
```

看到 `AI Photo Editor: 0.0.0.0:8000` 後保持這個終端開啟。若暫時不使用 Ollama，可改用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\backend\start_backend_mobile.ps1 -DisableLlm
```

### 5. 連接 Android 手機

1. 在手機開啟「開發人員選項」與「USB 偵錯」。
2. 用可傳輸資料的 USB 線連接電腦。
3. 手機出現授權提示時，允許這台電腦進行 USB 偵錯。
4. 開啟終端 B 並執行：

```powershell
flutter devices
```

記下 Android 手機的裝置 ID。清單中沒有手機時，先檢查 USB 模式、偵錯授權及 Windows USB 驅動，不要改用本機模擬器。

### 6. 安裝並執行 App

在終端 B 執行，將 `<裝置ID>` 與 `<電腦IPv4>` 換成實際值：

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject\mobile_app
flutter run --no-pub -d <裝置ID> --dart-define=API_BASE_URL=http://<電腦IPv4>:8000
```

範例：

```powershell
flutter run --no-pub -d R58M123456A --dart-define=API_BASE_URL=http://192.168.1.25:8000
```

Flutter 會自動建置、安裝並開啟 App。結束執行時在終端 B 按 `Ctrl+C`。

### 7. 在 App 內確認後端

1. 點選「更多操作」。
2. 開啟「後端連線設定」。
3. 確認網址為 `http://<電腦IPv4>:8000`。
4. 點「檢查連線」，成功後儲存。
5. 選擇一般 JPEG 或 PNG 原圖，執行一次文字修圖，確認能顯示結果。

若連線失敗，確認終端 A 仍在執行、兩台裝置使用同一個 Wi-Fi、IP 沒有輸錯，並檢查 Windows 防火牆是否允許私人網路的 8000 port。

## 目前沒有手機時

沒有實體裝置時只能做靜態分析、自動測試與 APK 建置，不能驗證相簿、麥克風、觸控或真機網路：

```powershell
cd C:\Users\User\Desktop\AI_image_editor_gradproject\mobile_app
flutter analyze --no-pub
flutter test --no-pub
flutter build apk --debug --no-pub
```

Debug APK 會位於：

```text
mobile_app/build/app/outputs/flutter-apk/app-debug.apk
```

取得實體手機後仍需依上面的真機流程完成手動驗收。完整待測清單在 [`../docs/mobile/README.md`](../docs/mobile/README.md)，另開測試聊天時使用 [`../docs/mobile/TEST_PROMPT.md`](../docs/mobile/TEST_PROMPT.md)。
