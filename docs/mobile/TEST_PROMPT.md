# 新聊天測試 Prompt

```text
請對 AI Photo Editor 手機版做獨立測試與驗收。

專案：C:\Users\User\Desktop\AI_image_editor_gradproject

先閱讀 docs/mobile/README.md、mobile_app/README.md、專案指引及 git status。
原網頁版是 frontend/；獨立手機版是 mobile_app/。不得把手機修改寫回 frontend/。
保留既有未提交修改、研究資料、模型、照片與歷史。使用專用測試圖和新 session。

重要主機限制：2026-09-09 啟動 Android Emulator 後 Windows 發生非正常關機，事件為 Kernel-Power 41，沒有 bugcheck 或 dump。不得自行啟動 emulator，也不得修改 AEHD、Hyper-V、BIOS、GPU 或虛擬化設定。Android 原生操作只能使用使用者明確提供的實體裝置；若沒有裝置，將安裝與功能項目標為受阻。

1. 記錄 commit（若有）、git diff、裝置、OS、Flutter/Dart/JDK/SDK、Python與相依。
2. 先在 frontend 執行 flutter analyze 與 flutter build web --release --no-pub；用 Spec 內的指令開啟 Web，做畫面和主要修圖流程 smoke test，結束後確認 frontend 仍無追蹤檔差異。
3. 執行 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_mobile.ps1`；確認它只修改 mobile_app，不修改 frontend。
4. 在 mobile_app 執行 flutter doctor -v、flutter devices、flutter analyze；只列出裝置，禁止啟動 emulator。
5. 建置 debug/release APK；只有使用者提供實體 Android 時才能安裝，並記錄產物。不要把建置成功當成功能通過。
6. 以 `powershell -NoProfile -ExecutionPolicy Bypass -File backend/start_backend_mobile.ps1` 啟動既有單 worker 後端，測試真機同 Wi-Fi 連線。
7. 逐項測 docs/mobile/README.md 的 T01～T12 和 MOB-01～06。

必測細節：
- 先證明 frontend 和原本 Web 畫面／功能沒有因 mobile_app 改變。
- 後端 URL 驗證、8 秒 health、設定保存優先序、取消改址、A/B 後端 session 不混用。
- JPEG/PNG 正常與邊界、偽副檔名、損壞、HEIC/RAW、EXIF、透明 PNG、大圖。
- Android 選原圖／參考圖時 Activity 被回收，確認角色不混用。
- 匯出選定正式版本；preview／草稿不能誤存；權限拒絕、空間不足、重複點擊、逾時。
- 比對後端結果和相簿檔案的內容、格式、像素尺寸，確認不是畫面截圖且沒有再次縮放。
- 最近工作恢復、原圖基準、版本不存在、session 404、斷網重試、清除、換後端。
- 語音權限、15秒、取消、背景、鎖屏和過期辨識回應。
- 回歸文字、參考圖、雙模型、後端目前回傳的 50 筆風格、15個參數、局部修圖、比較、歷史分支、Photo Git、修圖限制、語言與主題；若數量改變，以 `/edit/styles` 實際資料為準並記錄差異。
- 測網路逾時、後端重啟、處理中操作、預覽不入歷史、錯誤不污染版本。
- 記錄12MP／24MP、模型冷暖啟動、滑桿預覽、記憶體及長時間操作數據。
- iOS 缺 Mac 或裝置時標記受阻，不可推測通過。

可新增有實際價值的 controller、widget、integration 和後端相容性測試；產品缺陷交回實作聊天修改後再複測。模擬 API 與真實後端／模型結果分開記錄。系統權限和相簿無法自動操作時，提出明確人工步驟並等待結果。

交付 docs/mobile/TEST_REPORT.md：
- 測試版本、環境、指令、資料與證據。
- T01～T12、MOB-01～06 的通過／失敗／未測／受阻矩陣。
- 缺陷嚴重性、重現步驟、預期／實際、日誌／截圖／輸出。
- 區分手機缺陷、原有功能缺陷和環境阻擋。
- 修正清單、複測範圍，以及是否符合專題展示標準。
```
