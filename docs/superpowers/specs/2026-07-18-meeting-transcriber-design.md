# 會議記錄 App — 設計規格

**日期**：2026-07-18
**狀態**：待使用者確認

## 一句話

一個掛在 GitHub Pages 的手機網頁 App（PWA），把會議錄音檔上傳後，透過使用者自己的 Gemini API 金鑰產生**繁體中文逐字稿**與**結構化摘要**，並把每場會議記錄存在手機本機，可離線瀏覽。

## 目標使用者與情境

- 單一使用者（本人），iPhone（iOS Safari）。
- 事後上傳：開會時已用手機錄音，事後把音檔丟進 App 取得逐字稿＋摘要。
- 不需要即時字幕。
- 不想付錢：使用 Gemini 免費層 API 金鑰即可（付費層可用、額度更高，但非必要）。

## 硬限制與前提（為什麼是這個架構）

- ownscribe 原專案是跑在 macOS 的本機 Python + 多 GB ML 模型程式，**無法**移植到 GitHub Pages（靜態託管、無伺服器運算）或 iPhone 瀏覽器。
- 因此「運算」外包給 Gemini 雲端 API；App 本身是**純前端靜態網站**。
- Gemini 免費層（Google AI Studio 金鑰）：每日 1,500 次、免綁信用卡；音訊最長支援約 9.5 小時；支援瀏覽器端以 API 金鑰直接呼叫。
- 使用者已有一組現成免費層金鑰（AI Studio 專案 SHIANG，金鑰名 KEN）。

## 整體架構

純前端靜態網站，無後端、無建置步驟（plain HTML + CSS + JavaScript），方便 GitHub Pages 託管與長期維護。

```
[iPhone Safari / 主畫面 PWA]
      │
      ├─ App 殼 (index.html + manifest + service worker) → 可加到主畫面、離線瀏覽
      ├─ 設定：Gemini API 金鑰（存 localStorage）
      ├─ 上傳音檔 → 送 Gemini（Files API 上傳 + generateContent）
      │        └─ 回傳：逐字稿 + 結構化摘要(JSON)
      ├─ 儲存：IndexedDB（每場會議一筆，不含原音檔）
      └─ 瀏覽：會議清單 / 單場詳情 / 匯出備份
                                   │
                          [Google Gemini API]（使用者金鑰）
```

## 模組拆解（各自單一職責、可獨立測試）

1. **設定模組 (settings.js)**
   - 職責：讀寫 Gemini API 金鑰於 `localStorage`；首次使用引導輸入。
   - 介面：`getApiKey()`, `setApiKey(key)`, `hasApiKey()`。

2. **Gemini 客戶端 (gemini.js)**
   - 職責：把音檔上傳到 Gemini Files API，呼叫 `generateContent` 取得逐字稿＋摘要。
   - 為什麼用 Files API：會議音檔常超過 20MB 內嵌上限，Files API 支援長音檔（免在瀏覽器內做失真重壓縮）。
   - 使用 JSON 結構化輸出（responseSchema）確保回傳可直接存檔。
   - 模型：Gemini 具音訊能力的 Flash 模型（實作時依當時官方文件確定精確 model id）。
   - 介面：`transcribeAndSummarize(file, {onProgress}) → {transcript, summary}`。
   - 錯誤處理：無金鑰、金鑰無效、額度用盡、檔案格式不支援、網路失敗、Files API 處理中輪詢。

3. **儲存模組 (store.js)**
   - 職責：IndexedDB CRUD。
   - 資料結構：
     ```
     Meeting {
       id: string,
       title: string,          // 預設用檔名或日期，可編輯
       createdAt: number,      // epoch ms
       transcript: string,     // 繁體中文逐字稿
       summary: {
         keyPoints: string[],  // 重點條列
         actionItems: string[],// 待辦事項
         decisions: string[]   // 決議事項
       }
     }
     ```
   - **不儲存原始音檔**（使用者手機已有錄音檔）。
   - 介面：`list()`, `get(id)`, `save(meeting)`, `remove(id)`, `exportAll()`。

4. **UI 模組 (ui.js + index.html + styles.css)**
   - 首頁：會議清單（標題、日期、摘要片段），「＋ 新增」按鈕。
   - 新增流程：選音檔 → 顯示進度（上傳中／辨識中）→ 完成後存檔並跳到詳情。
   - 詳情頁：摘要（重點／待辦／決議三區塊）＋ 逐字稿，各區有「複製」按鈕；可改標題、刪除。
   - 設定頁：輸入／更新 API 金鑰。
   - 匯出備份：把所有會議記錄下載成一個 JSON 檔。
   - 手機優先版面（大按鈕、單欄、適配 iOS safe-area）。

5. **PWA 殼 (manifest.webmanifest + sw.js)**
   - 加到 iPhone 主畫面、離線可開啟並瀏覽已存記錄（辨識仍需連網）。

## 使用者流程

1. 主畫面開啟 App。
2. 首次：輸入 Gemini 免費金鑰（存本機，之後免再輸）。
3. 按「＋ 新增」→ 從 iPhone 檔案／語音備忘錄選會議錄音。
4. App 上傳並辨識 → 回傳繁中逐字稿＋摘要。
5. 自動存成一筆會議記錄，導向詳情頁。
6. 之後可在清單重看、複製、改標題、刪除、匯出備份。

## 錯誤處理

| 情況 | 行為 |
|---|---|
| 未設定金鑰 | 導向設定頁提示輸入 |
| 金鑰無效 / 額度用盡 | 顯示清楚錯誤訊息與建議（換金鑰或稍後再試）|
| 音檔格式不支援 | 提示支援的格式（mp3/m4a/wav 等）|
| 檔案處理中 | 輪詢 Files API 狀態，顯示「辨識中…」進度 |
| 網路失敗 | 提示重試，不遺失已輸入資料 |
| iOS 清除本機資料風險 | 提供匯出備份；於設定頁說明 |

## 測試策略

- 核心純邏輯（儲存序列化、摘要 JSON 解析、檔案大小判斷）以輕量 JS 測試驗證。
- Gemini 呼叫以可注入的假回應測試模組行為（不打真 API）。
- 手動端到端：以一段真實會議錄音在 iPhone Safari 驗證整條流程。

## 部署

- GitHub 儲存庫 + 啟用 GitHub Pages（根目錄或 `/docs`）。
- 純靜態、無建置，push 即部署。

## 追加需求（2026-07-18 使用者確認後納入）

- ✅ **長錄音優先設計（常態 1 小時以上）**：一律走 Gemini Files API 串流上傳（支援至 9.5 小時、大檔不佔滿手機記憶體）；`generateContent` 設 `maxOutputTokens:65535` 並 `thinkingConfig.thinkingBudget:0`（關閉預設思考，避免思考 token 吃掉輸出額度導致長逐字稿被截斷）；逐字稿被截斷時回傳可理解的錯誤提示分段上傳。
- ✅ **語者辨識（2 人以上）**：逐字稿改為 `segments: {speaker,text}[]`，Gemini prompt 要求標記「說話者1／2／3…」；詳情頁依說話者分色顯示。
- ✅ **手機穩定執行**：辨識期間取得 Screen Wake Lock 防止螢幕鎖住中斷；清楚進度與真實錯誤訊息。

## 範圍外（先不做，之後可加）

- ❌ 即時字幕（本版為事後上傳）。
- ❌ 帳號 / 多裝置雲端同步（本版為單機記憶＋手動匯出備份）。
- ❌ 儲存原始音檔。

## 主要風險

1. **Gemini Files API 的瀏覽器端 CORS / 上傳流程**：需在實作時先以最小範例驗證從靜態頁可成功上傳長音檔；若受阻，退路為「瀏覽器內降頻壓縮至 20MB 內走內嵌上傳」。
2. **iOS 本機資料被清除**：以匯出備份緩解。
3. **免費額度或模型 id 變動**：模型 id 於實作時依當時文件確認。
