# AI Photo Editor Mobile

這是從既有網頁版獨立出來的 Flutter 手機 App。原網頁版仍位於 `../frontend/`，手機功能只能在本目錄修改，避免影響網頁版本。

完整規格、實作紀錄與待測清單：[`../docs/mobile/README.md`](../docs/mobile/README.md)。

在專案根目錄準備手機專案（只解析套件與產生必要檔案）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\prepare_mobile.ps1
```

啟動既有後端：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\backend\start_backend_mobile.ps1
```

手機與電腦連同一個 Wi-Fi，在 App 的「更多操作 → 後端連線設定」輸入 `http://電腦區網IPv4:8000`。Android 模擬器使用 `http://10.0.2.2:8000`。

不使用模擬器的自動測試與 APK 建置結果記錄在 [`../docs/mobile/TEST_REPORT.md`](../docs/mobile/TEST_REPORT.md)。安裝、相簿、麥克風與觸控操作仍需實體裝置驗收。
