# 單字訂正卡（待確認清單改版）設計

日期：2026-10-06。依據：使用者回饋「整句＋名稱無依據」沒有意義；第二次稽核（audit2）發現確認名詞只改逐字稿不改摘要、隱含數字矛盾與人名異寫未被抓到、自動校正只改部分出現處、未辨識段落未揭露。

## 使用者流程
待確認清單每張卡 = 一個「詞／片段」的訂正建議：
- 顯示：上下文片段（被標示的詞以【】標出）、建議（改成 X／保留／刪除這段）、一句理由（必含時間點與逐字稿原話）、影響範圍（逐字稿 N 處、摘要 M 條）。
- 按鈕：✅ 照建議（主按鈕）／保留原文／✏️ 自己改（輸入替換文字）。
- 同一個 span→suggestion 只出一張卡（跨條目合併）。

## 資料（ReviewItem v2，取代舊 kind 卡片呈現；舊欄位保留相容）
review.items[i] 新增：
- span: str（要處理的原文片段，必須是逐字稿或條目中實際存在的字串）
- action: "replace" | "keep" | "delete"
- suggestion: str（action=replace 時的新文字；delete 時為空）
- reason: str（≤ 60 字，含 [HH:MM:SS] 與原話）
- confidence: "high" | "medium" | "low"
- targets: [{where: "transcript"|"summary.<k>"|"notes.<k>", itemId?: str, count: int}]
- context: str（≤ 40 字，span 以【】標出）
- sources: [原本觸發的旗標 kind…]（term／name／number／quote／calc／slip／ai）

## 伺服器流程
reprocess 在 verify 之後加一步 adjudicate（Opus）：
1. 輸入：程式旗標（verify 的 name/number/quote/calc）、pending corrections、slips，加上每條摘要條目與其 src 前後 ±60 秒逐字稿；另附「全份人名清單（逐字稿中出現的人名與其異寫）」。
2. 同時做 AI 審稿：逐條摘要對照原句，找人名異寫、隱含數字矛盾（如 48%×60億≠24億）、前後矛盾、把總額與部分說成矛盾等。
3. 輸出 word-level issues；伺服器正規化：span 必須在目標文字中存在（不存在則丟棄或降為 keep）、合併同 span→suggestion、計算 targets。
4. 程式旗標若被 AI 判為「保留」，仍出卡（action keep，使用者一鍵確認）。

## 套用
POST /api/meetings/{id}/review/{rid} {action: "accept"|"keep"|"edit", text?}：
- accept（照建議）：replace → 在逐字稿全部出現處（排除位於既有 suggestion 內者）與所有 summary/notes 條目文字中替換；delete → 從條目中刪除該片段（不刪逐字稿）；keep → 只標記 confirmed。
- edit：以使用者文字當 suggestion，同 accept。
- 套用後純程式重驗（verify_review），不再呼叫 AI。
- 舊 action（confirm/edit/delete）保留相容。

## 自動校正剩餘處
已套用的 correction 若逐字稿仍有 wrong 出現處 → 產生一張卡（replace，理由「已自動改 N 處，另有 M 處未改」）。

## 未辨識段落
伺服器從 transcript 計算相鄰段落間隔 > 20 秒（第一段前、最後一段後也算）→ meeting.gaps=[{start,end}]；md/docx 匯出開頭列「約 X 分鐘未辨識：HH:MM:SS–HH:MM:SS…」；詳細頁顯示同一行。

## 前端
- 待確認清單卡片改為上述單字訂正卡（舊格式 review item 沒有 span 時，退回舊呈現）。
- 「照建議」為大按鈕；按下即送出並刷新；顯示「已改：逐字稿 N 處、摘要 M 條」。
