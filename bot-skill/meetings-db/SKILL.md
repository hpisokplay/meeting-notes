---
name: meetings-db
description: 會議記錄資料庫。使用者問會議、會議紀錄、開會、逐字稿、摘要、待辦、決議、「上次開會」「某某會議講了什麼」「找會議」「把會議記錄傳給我」「要 Word 檔」「要錄音」，或傳錄音檔／音檔／語音來要轉成會議記錄時使用。資料在 /projects/meetings/。
---

# 會議記錄資料庫

資料夾：`/projects/meetings/`（唯讀為主，絕對不要修改或刪除任何會議檔）

```
index.json            會議清單 [{id,title,createdAt(毫秒),group,durationSec,status}]
meetings/<id>.json    會議主檔（完整資料）
meetings/<id>.md      純文字版：標題、日期、摘要、逐字稿（含說話者與時間）← 回答問題讀這個
exports/<id>.docx     Word 匯出（還有待確認項目時不會有這個檔）
audio/<id>/<n>-<檔名>  原始錄音
groups.json           分類 [{id,name,color}]
inbox/                丟錄音進來會自動建立會議並轉錄
```

網頁連結：`https://ddys.tail95e8ec.ts.net:9443/#/m/<id>`

## 查詢
1. 讀 `index.json` 依標題／日期（createdAt 是毫秒，換算台北時間）／分類找會議。`status` 不是 `done` 代表還在轉錄或處理中，要告訴使用者。
2. 讀 `meetings/<id>.md` 回答。
3. 跨會議搜尋關鍵字：`grep -n -i "關鍵字" /projects/meetings/meetings/*.md`，再用檔名（= id）回 index.json 對應標題。
4. 「最近的會議」就按 createdAt 由新到舊排。

## 要檔案
- Word：先確認 `exports/<id>.docx` 存在（`ls /projects/meetings/exports/<id>.docx`）。
  - 存在：`cp /projects/meetings/exports/<id>.docx /projects/_outbox/`
  - 不存在：代表這場還有待確認的內容（伺服器在待確認清單清空前不產生 Word 檔）。讀 `meetings/<id>.json` 的 `review.items`，數 `state` 為 `"pending"` 的項目數 N，回覆「這場還有 N 項待確認，請先到網頁處理完再傳」並附網頁連結。
  - 絕對不要拿 .md 文字版代替 Word 檔交差，也不要自己把 .md 轉成 Word：沒確認過的內容不能給董事長。
- 文字版：`cp /projects/meetings/meetings/<id>.md /projects/_outbox/`（第一行若是「⚠️ 尚有 N 項待確認，內容未經確認，請勿轉交」，傳之前要先把這句話原樣告訴使用者；要轉給董事長的一律用上面的 Word 流程）
- 錄音：只有檔案 ≤ 45 MB 才 `cp` 到 `/projects/_outbox/`（先 `ls -l` 確認大小）；超過就只給網頁連結。
- 放進 `_outbox/` 後這次任務結束，系統會自動傳給使用者。檔名維持原樣即可。

## 傳錄音進來
使用者在 Telegram 傳錄音／音檔（≤ 20 MB，Telegram 下載上限）要轉成會議記錄時：
1. 把收到的檔案先複製成暫存名：`cp <收到的檔> /projects/meetings/inbox/<檔名>.tmp`
2. 再改成正式名：`mv /projects/meetings/inbox/<檔名>.tmp /projects/meetings/inbox/<檔名>`（避免伺服器讀到寫一半的檔）
3. 回覆「已丟進去，轉好會通知你」。伺服器會自動建立會議並轉錄，完成後自己傳通知。
- 超過 20 MB 的檔案請使用者改從網頁上傳：`https://ddys.tail95e8ec.ts.net:9443/#/new`
- 檔名含空白或中文沒問題，但不要覆蓋 inbox 內已有的同名檔。

## 回答規則
- 一律繁體中文、短條列，先給結論。
- 引用內容要附時間戳與說話者（例如 `[12:34] 王經理：…`），不要編造 .md 裡沒有的內容；找不到就說找不到。
- 回答會議時附上網頁連結。
- 絕對不修改、移動、刪除 `/projects/meetings/` 內既有檔案（只能新增 inbox 檔、複製出去）。
- 語音辨識可能有錯字，專有名詞存疑時註明「逐字稿可能誤聽」。
