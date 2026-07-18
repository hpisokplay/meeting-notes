# 會議記錄 App 🎙️

掛在 GitHub Pages 的手機網頁 App（PWA）。上傳會議錄音檔，透過你自己的 Gemini API 金鑰產生**繁體中文語者分段逐字稿**與**摘要（重點／待辦／決議）**，記錄存在手機本機、可離線瀏覽。

專為**長會議（1 小時以上）**與**多位說話者**設計。

## 使用方式

1. 用 iPhone Safari 開啟網址。
2. 右上角 ⚙︎ 設定，貼入 Gemini API 金鑰（[aistudio.google.com/apikey](https://aistudio.google.com/apikey) 免費申請）。
3. ＋ 新增會議 → 從「檔案／語音備忘錄」選錄音檔 → 開始辨識。
4. 得到語者分段逐字稿 + 摘要，可複製、改標題、刪除、匯出備份。
5. Safari 分享鈕 →「加入主畫面」，即可像原生 App 使用。

> 辨識長會議需數分鐘，過程請保持螢幕開啟、勿切換 App（系統會嘗試用 Wake Lock 維持螢幕）。

## 技術

- 純前端靜態網站（HTML + CSS + Vanilla JS，ES Modules），無建置步驟。
- 語音辨識與摘要：Google Gemini API（`gemini-2.5-flash`，Files API 上傳長音檔）。
- 儲存：IndexedDB（本機）；PWA 離線殼：Service Worker。

## 開發

```bash
npm install
npm test                 # 執行單元 + 整合測試
python -m http.server 8000   # 本機預覽 http://localhost:8000
```

## 隱私

音檔與逐字稿只在你的手機與你自己的 Gemini API 之間傳輸，App 本身不經過任何第三方伺服器。金鑰只存於瀏覽器 localStorage。
