# AI Photo Editor Mobile 無裝置測試報告

- 測試日期：2026-09-14（Asia/Taipei）
- Git 基準：`1d027c9e19477e271ce3677a0391c65bdfcf3eff`
- 測試範圍：`mobile_app/`、共用 `backend/`，以及唯讀驗證 `frontend/`
- 裝置狀態：沒有實體 Android／iPhone；Android Emulator 因曾導致 Windows 非正常關機而禁止啟動

## 結論

手機版在目前可由主機驗證的範圍內，已保留網頁版的核心修圖功能介面與後端契約：6 個工具種類一致、14 個 API 方法一致、8 個共用 Dart 檔逐位元相同，Web 原有多語系文字全部保留。共用後端的圖片上傳、文字修圖、PNG 結果、session、手動預覽與手動提交均已實際成功。

目前不能宣告「手機端完整功能已全部通過」。相簿選圖與儲存、麥克風、觸控手勢、Activity 回收、橫直向、小螢幕、真機網路和 App 冷啟動都必須在實體裝置上驗收。這些是環境受阻，不是本輪發現的手機程式缺陷。

後端實際提供 50 筆風格，舊規格寫 52 筆。Web 與 Mobile 共用同一個 `/edit/styles`，所以兩端目前一致為 50；相關規格已修正。

## 測試環境

| 項目 | 版本／結果 |
|---|---|
| 作業系統 | Windows，PowerShell |
| Flutter | 3.47.2 stable |
| Dart | 3.13.2 |
| Java | Temurin OpenJDK 17.0.20.1 |
| Python | 3.13.7，專案根目錄 `.venv` |
| Android SDK | compile/target API 36、minSdk 24 |
| Android 裝置 | 無；未啟動 Emulator |
| iOS 工具鏈 | Windows 無法建置，受阻 |

Android Studio 隨附 JDK 25，但 Gradle 8.14 無法使用該版本；Flutter 已設定為 JDK 17，之後 Debug 與 Release APK 均建置成功。

## 自動化與建置證據

| 檢查 | 結果 |
|---|---|
| `flutter analyze --no-pub`（Mobile） | 通過，`No issues found!` |
| `flutter test --no-pub --reporter expanded` | 11/11 通過 |
| `python tools/check_mobile_parity.py` | 通過 |
| `flutter build apk --debug --no-pub` | 通過 |
| `flutter build apk --release --no-pub` | 通過 |
| APK Signature Scheme v2 驗證 | Debug／Release 均通過 |
| `flutter build web --release --no-pub` | 通過，產生 `frontend/build/web` |
| Web 追蹤檔差異 | 無；沒有修改 `frontend/` |

Parity 檢查的詳細結果：

- 8 個共用 Dart 檔 byte-identical。
- `EditorApi` 的 14 個方法一致。
- 工具集合一致：文字修圖、雙模型、風格、參考圖、手動調整、歷史。
- 英文 390、繁中 364、台灣繁中 364 個 Web ARB 項目和值皆保留；Mobile 各增加 35 個手機專用文字。
- Android 權限與 iOS usage description 靜態檢查通過。

11 項 Flutter 測試涵蓋：後端 URL 正規化與拒絕無效 URL、伺服器/session/選圖角色保存、health 契約、錯誤結構、結果 URL、PNG 解碼與原尺寸、錯誤格式、損壞圖片、40 MiB 上限及 24 MP 上限。

APK 產物：

| 產物 | 大小 | SHA-256 |
|---|---:|---|
| `mobile_app/build/app/outputs/flutter-apk/app-debug.apk` | 158,797,786 bytes | `A30988E5640C146F4D8FDF51C50CCEF9B8623EDC7A0108D041396AEB0B79753D` |
| `mobile_app/build/app/outputs/flutter-apk/app-release.apk` | 56,614,212 bytes | `D85165930B2517B54294BE571D66139ECCB950CB44213A96FCC3DDFF4DA7A446` |

兩個 APK 的 package 為 `tw.edu.gradproject.aiphotoeditor`，版本為 `1.0.0+1`，minSdk 24、targetSdk 36。Release 仍以 Android debug 憑證簽署，只適合展示與測試，不適合商店發布。

## 真實後端 smoke test

後端以 `AI_PHOTO_USE_LLM=0`、`127.0.0.1:8765`、單 worker 啟動，使用專案既有 `airy_daylight.jpg` 建立新的測試 session，沒有覆寫 Web 程式碼或既有照片。

| 流程 | 實際結果 |
|---|---|
| `GET /health` | HTTP 200，`{"status":"good"}` |
| `POST /edit`，JPEG + `increase brightness` | 成功；OpenCV 套用 brightness 18，產生 PNG |
| `GET /edit/sessions/{id}` | 成功；提交後 session 有 2 個版本 |
| `POST /edit/manual/preview` | 成功；brightness 5，產生 preview，未寫入正式歷史 |
| `POST /edit/manual/commit` | 成功；建立子版本且 `parent_edit_id` 正確 |
| 下載 `result_url` | HTTP 200，`image/png`，499,317 bytes |
| `GET /edit/manual/schema` | 15 個手動參數 |
| `GET /edit/contracts/schema` | 4 個修圖限制 metrics |
| `GET /edit/styles` | 50 筆風格 |
| `GET /edit/auto-models/health` | 兩個模型資產狀態為 ready；本輪未執行重型模型比較 |

測試 session：`session_eb66391b6e1e417c8c60ffd1e6a44d52`。第一個文字修圖版本為 `edit_dac5fe7e8e7d4d3c8e1b2308a26a4c46`，手動提交版本為 `edit_96adbe7c8f7b4d2790678d87a8c72979`。

## T01～T12 驗收矩陣

| ID | 狀態 | 證據／尚缺項目 |
|---|---|---|
| T01 | 部分通過 | Web analyze 與 release build 已通過，且 `frontend/` 無差異；瀏覽器人工互動未測 |
| T02 | 部分通過 | Debug／Release APK 與簽章通過；無實機可安裝、冷啟動 |
| T03 | 部分通過 | URL、health、保存與結構化錯誤通過；真機斷網與切換兩台後端未測 |
| T04 | 通過（主機範圍） | JPEG/PNG 簽章、損壞／錯誤格式、40 MiB、24 MP 邊界通過；HEIC/RAW 依規格不支援 |
| T05 | 受阻 | EXIF 顯示方向、透明圖實際顯示、Activity 回收和大圖記憶體需實機 |
| T06 | 部分通過 | 真實 PNG 結果與 URL 通過；相簿權限、正式版本選擇和檔案內容比對需實機 |
| T07 | 部分通過 | 保存資料及真實 session 讀取通過；真機上的重啟、404、斷網與清除流程未測 |
| T08 | 受阻 | 權限與語音程式契約存在；麥克風、15 秒、背景／鎖屏需實機 |
| T09 | 部分通過 | 6 工具、14 API、共用 UI/模型、多語系對等；OpenCV 真實流程通過，雙 AI 模型只驗證 ready |
| T10 | 受阻 | 小螢幕、橫直向、鍵盤、大字體、安全區域、返回鍵與手勢需實機 |
| T11 | 部分通過 | 24 MP 防護通過；12/24 MP 真實效能、模型冷暖啟動及長時間操作未測 |
| T12 | 受阻 | Windows 無 Xcode、Mac 與 iOS 裝置 |

## MOB-01～MOB-06 驗收矩陣

| ID | 狀態 | 證據／尚缺項目 |
|---|---|---|
| MOB-01 後端連線 | 部分通過 | URL 驗證、health 與真實 localhost 後端通過；區網真機未測 |
| MOB-02 圖片匯入 | 部分通過 | 格式、容量、像素限制通過；Android picker 與 lost-data 恢復需實機 |
| MOB-03 相簿匯出 | 部分通過 | 正式結果 URL、PNG 與原檔下載通過；`gal` 寫入及權限需實機 |
| MOB-04 最近工作恢復 | 部分通過 | SharedPreferences 隔離 server/session/selected edit 與 picker role 通過；重啟 UI 需實機 |
| MOB-05 語音與生命週期 | 受阻 | Android/iOS 權限宣告存在；錄音及生命週期行為需實機 |
| MOB-06 網路與操作一致性 | 部分通過 | API 契約、錯誤結構和共用功能對等；逾時及操作中的觸控行為需實機 |

## 發現與限制

1. **規格資料落差（已修文件）**：舊文件聲稱 52 筆風格，實際共同後端為 50 筆。兩端功能相同，沒有 Mobile 專屬缺漏。
2. **發行簽章（已知限制）**：Release APK 使用 debug key。展示安裝可用，公開發布前要建立正式 keystore 與安全的簽署流程。
3. **工具鏈維護（低風險）**：Flutter 建置提示 Gradle 8.14、Android Gradle Plugin 8.11.1、Kotlin 2.2.20 將在未來版本停止支援；目前建置成功，本輪未做版本升級。
4. **原生驗收阻擋**：缺實體裝置，因此不能驗證相簿、麥克風、手勢、生命週期及真機網路。禁止用目前 AVD 代替，避免再次造成主機當機。

本輪沒有發現可由無裝置測試重現的 Mobile 功能缺陷，也沒有修改 `frontend/`。要達到專題展示的完整驗收標準，仍需借用一台 Android 7.0（API 24）以上真機，依 [TEST_PROMPT.md](TEST_PROMPT.md) 補完所有「受阻」與「部分通過」項目。
