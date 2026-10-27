# DD會議紀錄（NAS 版）🎙️

放在家裡 Synology NAS 上的會議紀錄 App（PWA）。上傳會議錄音，**電腦顯示卡**轉成分說話者的逐字稿，**Claude（Max 訂閱）**整理摘要、學習筆記、問答、專有名詞訂正與翻譯。資料存在 NAS，手機與電腦開同一個網址看到同一份。

網址（Tailscale 內網）：`https://ddys.tail95e8ec.ts.net:9443/`

## 使用方式

1. 手機或電腦連上 Tailscale，開啟上面的網址。iPhone 用 Safari 分享鈕 →「加入主畫面」就能像 App 一樣用。
2. ＋ 新增會議 → 選錄音檔（可多選，依檔名順序接成一場；單檔可超過 1 GB、8 小時）→ 上傳並開始轉錄。
   上傳是分塊的，中斷了重選同樣的檔案會從中斷處續傳；上傳時請保持螢幕開啟。
3. 上傳完就可以關掉 App。進度頁會顯示「排隊中（等電腦轉錄器）→ 轉錄中 → 摘要中 → 完成」；電腦沒開時 NAS 會在等待時間後自己用備援轉錄（較慢、不分說話者），之後可按「重新轉錄」改用電腦。
4. 詳情頁：摘要／學習筆記、加強、換版／合併、編輯刪除、說話者改名、逐句修改、專有名詞訂正、問這場會議、翻譯、PDF／Word 匯出、錄音播放（點逐字稿的時間標記就從那裡播）。
5. 設定頁：NAS 與轉錄器狀態、Claude 型號（sonnet／opus／haiku）、NAS 備援轉錄與等待分鐘、Telegram 通知、匯入舊版備份、匯出備份、強制更新。

## 架構

- `index.html`、`css/`、`js/`：純前端（HTML + CSS + Vanilla JS ES Modules），無建置步驟，由 NAS 上的 FastAPI 伺服器在同一個網址提供。
- `js/api.js`：與伺服器溝通的唯一通道（`/api/*`，分塊上傳、AI 工作輪詢）。
- `js/store.js`：IndexedDB **只當快取**（先秒開上次的畫面，再向 NAS 更新；連不上時可離線瀏覽）。資料主本在 NAS。
- `sw.js`：PWA 離線殼；`/api/*` 與錄音一律走網路、不快取。
- 伺服器（`server/`）、電腦轉錄器（`worker/`）見 `docs/superpowers/specs/2026-10-05-nas-migration-design.md`。

## 開發

```bash
npm ci
npx vitest run           # 前端單元 + 整合測試（jsdom，API 以假資料替代）
```

## 完整整理與 API 金鑰

「完整整理」是會議詳情頁的按鈕，AI 會把整場會議整理成一份完整報告。只有這一步使用 Claude API，其他 AI 處理仍走你的 Claude Max 訂閱。

- 金鑰請自己放進 NAS 的 `/volume1/docker/meetings/anthropic.key`（檔案權限 `chmod 600`），**不要貼到對話或 git**。步驟見 `deploy/nas/README.md`。
- 沒有金鑰或檔案是空的：自動改用 Max 產生，畫面標示「由 Max 產生」。
- 費用：每場會議一次 Opus 呼叫，長會議較貴，「重新處理」也會再呼叫一次（切換講座／會議不會）。
- 關閉：清空金鑰檔即可。使用 API 時，逐字稿會送到 Anthropic。

## 隱私

錄音、逐字稿與摘要只存在你自己的 NAS。AI 處理用你的 Claude 訂閱；設定了 API 金鑰時，「完整整理」改用 Claude API，這時逐字稿會送到 Anthropic。App 只在 Tailscale 內網開放。
