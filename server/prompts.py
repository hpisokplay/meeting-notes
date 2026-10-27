"""Prompts and JSON schemas ported from the legacy js/gemini.js (wording kept).

Differences from the Gemini version (Claude runs them via `claude -p --system-prompt`):
- the instructions are the system prompt; the transcript / payload arrives on stdin, so the
  trailing "逐字稿：" of the old prompts became a pointer to the user message;
- Gemini's Simplified->Traditional post-conversion is gone: every Chinese-output prompt
  carries LANG_RULE instead (translations into en/ja do not).
"""
from __future__ import annotations

LANG_RULE = "\n中文一律使用繁體中文（台灣用語），不可使用簡體字。"
IN_USER = "\n\n（逐字稿在使用者訊息中。）"

# ---------------------------------------------------------------- summary
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "actionItems": {"type": "array", "items": {"type": "string"}},
        "mainPoints": {"type": "array", "items": {"type": "string"}},
        "qa": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["actionItems", "mainPoints", "qa"],
}
SUMMARY_PROMPT = (
    "以下是一段會議逐字稿。請依內容整理成三類，並使用「與逐字稿相同的主要語言」"
    "（逐字稿主要是中文就用繁體中文、主要是英文就用英文、主要是日文就用日文）：\n"
    "- actionItems（待辦事項）：逐條列出，每項結尾標註「[DRI: 負責人]」，判斷不出負責人就寫「[DRI: 待指派]」（英文用 [DRI: TBD]）。\n"
    "- mainPoints（會議重點）：逐條列出，每條寫成「標題：說明」（標題為 4～14 字的名詞短語，接全形冒號再寫說明；英文用「Title: …」），每條只講一個議題。\n"
    "- qa（提問／Q&A）：格式「問：… 答：…」（英文用「Q: … A: …」），若沒有問答就回傳空陣列。"
    + LANG_RULE + IN_USER
)

# ---------------------------------------------------------------- enhance (summary sections)
SECTION_META = {
    "actionItems": {
        "label": "待辦事項",
        "instr": "逐條列出「所有」待辦／後續行動（action items），不要精簡、不要遺漏；每項結尾標「[DRI: 負責人]」，判斷不出就標「[DRI: 待指派]」（英文用 [DRI: TBD]）",
        "polishInstr": "- 每項結尾保留「[DRI: 負責人]」標註（英文用 [DRI: …]）；合併多條時，多位負責人可並列在同一個標註內。\n",
    },
    "mainPoints": {
        "label": "會議重點",
        "instr": "逐條列出「所有」重要重點與結論，力求完整、不要精簡；每條寫成「標題：說明」的點列格式"
                 "（標題為 4～14 字的名詞短語，點出這條在講什麼，後接全形冒號與說明；英文用「Title: 說明」）",
        "cap": 2,
        "polishInstr": (
            "- 每條必須是「標題：說明」的點列格式：先寫 4～14 字的名詞短語標題，接全形冒號，再寫說明（英文用「Title: …」）。不可寫成沒有標題的長篇論述。\n"
            "- 每條只講「一個」議題；不同議題（例如需求、技術優缺點、成本、市場數據）一律各自一條，不可壓縮成同一條。\n"
            "- 說明務必保留原始的具體資訊（數字、單位、比例、日期、人名、結論）。\n"
        ),
    },
    "qa": {
        "label": "會議提問 Q&A",
        "instr": (
            "把逐字稿中「每一組」提問與回答都抓出來（務必全部、不要只挑幾個），格式「問：… 答：…」（英文用「Q: … A: …」）。"
            "問題必須「離開逐字稿也看得懂」：把「此類形態」「這個」「那部分」「上述」等代名詞或指示詞，"
            "依上下文展開成具體名詞（例如「此類形態的產品」→「Brick 型態的伺服器電源」）；"
            "若上下文不足以完全確定所指，仍要依前後文做出最合理的推測，並在該詞後標註「（推測）」，不可原封不動留著看不懂的指示詞"
        ),
        "allowDrop": True,
        "polishInstr": (
            "- 維持「問：… 答：…」格式（英文用「Q: … A: …」）；針對同一議題的多組問答合併成一條，答案濃縮成重點結論。\n"
            "- 問題必須自足：仍帶有「此類」「這個」「那部分」等指涉不明的詞時，依清單其他條目的脈絡改寫成具體名詞；"
            "無法完全確定時，做出最合理的推測並標註「（推測）」，例如「問：Brick 型態的伺服器電源（推測）是否為貴司首款產品？」。\n"
            "- 價值篩選：把「不該進會議記錄」的條目標成 drop:true（text 留空字串）。符合以下任一即標 drop：\n"
            "  (a) 議程或流程安排（要不要先做簡介、能不能快速帶過、時間夠不夠、換下一頁、要不要休息）；\n"
            "  (b) 設備或連線確認（聽得到嗎、畫面有出來嗎）、純寒暄與出席確認；\n"
            "  (c) 答案只是複述問題或僅表示同意，問答雙方都沒有提供任何實質資訊。\n"
            "- 以下一律「不可」標 drop：涉及技術、規格、時程、成本、產能、商務條件、責任歸屬、風險的問答；"
            "答案為「尚未決定／待確認／再回覆」的也必須保留（那是有意義的狀態）。\n"
            "- 標 drop 應是少數；若你想標掉的超過三成，代表判斷過於嚴格，請重新檢視並只保留最明確的議程性條目。\n"
        ),
    },
}
ITEMS_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                "required": ["items"]}
MERGE_CAP = 3
POLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "src": {"type": "array", "items": {"type": "integer"}},
                    "drop": {"type": "boolean"},
                },
                "required": ["text", "src"],
            },
        },
    },
    "required": ["items"],
}


def enhance_prompt(meta: dict) -> str:
    return (f"以下是一段會議逐字稿。請{meta['instr']}。使用與逐字稿相同的主要語言。務求完整、不要遺漏、不要精簡。"
            '若這段沒有相關內容就回傳空陣列。只輸出 JSON {"items":[...]}。' + LANG_RULE + IN_USER)


def polish_prompt(meta: dict, n: int) -> str:
    cap = meta.get("cap") or MERGE_CAP
    return (
        f"以下是從會議逐字稿分批擷取的「{meta['label']}」原始清單，共 {n} 條（已編號）。請逐條改寫成正式會議記錄：\n"
        "- 每一條都改寫成精簡的書面語，刪除口語贅字（如「那個」「就是說」「嗯」），不要照抄逐字稿原文，但保留具體資訊（數字、日期、人名、結論）。\n"
        "- 只有當多條記錄的是「同一個問題／同一件事」（重複、追問、或同一件事分次提到）才可合併成一條；不同的問題即使屬於同一主題，也必須各自保留一條。合併是例外而非常態，輸出條數應與原始條數相近。\n"
        f"- 一條輸出最多合併 {cap} 條原始條目。\n"
        f"- 每條輸出都要在 src 列出它涵蓋的原始編號；{n} 條原始編號每一條都必須被涵蓋，不可遺漏、不可自行新增內容。\n"
        "- 使用與原始清單相同的主要語言。\n"
        + meta["polishInstr"]
        + '只輸出 JSON {"items":[{"text":"...","src":[編號]}]}。' + LANG_RULE
        + "\n\n（原始清單在使用者訊息中。）"
    )


# ---------------------------------------------------------------- ask
ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
ASK_PROMPT = (
    "你是會議記錄助理。請「只根據」使用者訊息中的會議逐字稿與摘要回答使用者的問題；"
    "會議中沒有提到的就明白說「會議中沒有提到」，不要自行編造。"
    "用使用者提問的語言回答，精簡、必要時分點。用純文字回答，不要使用 Markdown 符號（如 ** 或 #）。"
    "把回答放在 JSON 的 answer 欄位。" + LANG_RULE
)


def ask_user_message(summary: dict, transcript_text: str, question: str) -> str:
    s = summary or {}
    return (f"【摘要】\n待辦：{'；'.join(s.get('actionItems') or []) or '（無）'}\n"
            f"重點：{'；'.join(s.get('mainPoints') or s.get('keyPoints') or []) or '（無）'}\n\n"
            f"【逐字稿】\n{transcript_text}\n\n【使用者的問題】{question}")


# ---------------------------------------------------------------- terms
TERMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": ["person", "org", "product", "place", "country", "term"]},
                    "fix": {"type": "string"},
                },
                "required": ["term", "category"],
            },
        },
    },
    "required": ["items"],
}
TERMS_PROMPT = (
    "以下是一段會議逐字稿。請挑出裡面的「專有名詞」：人名(person)、公司或組織(org)、產品(product)、地名(place)、國家(country)、專業術語(term)。\n"
    "重點：優先挑出「可能被語音辨識聽錯或拼錯」的詞。一般常見字詞不要放。\n"
    '每筆回傳 {"term":"逐字稿中實際出現的寫法","category":"person|org|product|place|country|term","fix":"若你判斷它明顯拼錯/聽錯，給一個最可能的正確寫法；不確定就留空字串"}。\n'
    '只輸出 JSON {"items":[...]}。' + LANG_RULE + IN_USER
)
GROUP_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"terms": {"type": "array", "items": {"type": "string"}},
                               "best": {"type": "string"}},
                "required": ["terms"],
            },
        },
    },
    "required": ["groups"],
}
GROUP_PROMPT = (
    "以下是同一場會議逐字稿裡挑出的專有名詞清單。語音辨識常把同一個名字聽成好幾種寫法。\n"
    "請找出「其實指同一個實體」的寫法，把它們歸成一組。\n"
    "- 只在你有把握是同一個實體時才歸組（發音相近、字形相近、上下文明顯同指）。不確定就不要歸。\n"
    "- 每組的 terms 只能填「清單裡出現過的字串」，不可自行新增。\n"
    "- best 填這一組最可能的正確寫法（可以是清單裡沒有的正確字），無法判斷就留空字串。\n"
    "- 只有一種寫法、沒有變體的詞，不要放進 groups。\n"
    '只輸出 JSON {"groups":[{"terms":["…","…"],"best":"…"}]}。' + LANG_RULE
    + "\n\n（清單在使用者訊息中。）"
)

# ---------------------------------------------------------------- notes
FIGURE_RULE = (
    "只收「數字本身承載技術或商業資訊」的：規格、量測值、效能、成本、比例、時程、產能、市場規模。\n"
    "以下一律「不算」關鍵數據，不要列出：\n"
    "  (a) 會議進行的數量（幾位講者、第幾頁投影片、休息幾分鐘、Q&A 幾分鐘）；\n"
    "  (b) 數字只是名稱的一部分（5G、3D、Q&A、B200、EMIB-T 這類型號、世代或代號）；\n"
    "  (c) 沒有單位也沒有比較基準的孤立數字。\n"
    "每筆拆成三欄：label＝這個數字在講什麼（主題在前，不含數字）；value＝數值連同單位；\n"
    "group＝主題分組名稱（4～8 字，例如「市場規模」「產能與良率」「公司與團隊」「技術規格」），\n"
    "同一類的數據要用「完全相同」的 group 名稱，讓它們能排在一起互相對照。\n"
    '例如 {"group":"市場規模","label":"2030 年 FOPLP／GCS 預估","value":"81.1 億美元"}。\n'
    "⚠️ 若一筆數據本質是「A 與 B 的對比」（例如 620×750 mm 可容納 9 個、300×300 mm 只能容納 1 個），\n"
    "不要硬塞成一行關鍵數據，改整理到「對照表」裡。\n"
)
NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "outline": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "anchor": {"type": "string"},
                           "points": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "points"]}},
        "concepts": {"type": "array", "items": {
            "type": "object",
            "properties": {"term": {"type": "string"}, "plain": {"type": "string"}, "why": {"type": "string"}},
            "required": ["term", "plain"]}},
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"},
                           "headers": {"type": "array", "items": {"type": "string"}},
                           "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
            "required": ["title", "headers", "rows"]}},
        "figures": {"type": "array", "items": {
            "type": "object",
            "properties": {"group": {"type": "string"}, "label": {"type": "string"}, "value": {"type": "string"}},
            "required": ["label", "value"]}},
        "quiz": {"type": "array", "items": {
            "type": "object", "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
            "required": ["q", "a"]}},
    },
    "required": ["outline", "concepts", "tables", "figures", "quiz"],
}
NOTES_KEYS = ("outline", "concepts", "tables", "figures", "quiz")
NOTES_PROMPT = (
    "以下是一場研討會／課程的逐字稿。請整理成「複習用的學習筆記」，"
    "使用與逐字稿相同的主要語言（中文用繁體中文、台灣用語）。\n"
    "- outline（章節大綱）：依講述順序分節。title 為 8～20 字的主題；points 列出該節重點；"
    "anchor 填該節開始處逐字稿中的「一句原話」（10～20 字，必須是逐字稿裡真的出現過的字串，供跳轉定位，不可自行改寫；anchor 只放原句文字，不含時間與說話者）。\n"
    "- concepts（重要概念）：挑出需要理解的名詞或機制。term＝名詞本身；plain＝用白話說明它是什麼；why＝為什麼重要／用在哪裡。\n"
    "- tables（對照表）：把「多項目 × 多屬性」的內容還原成表格（技術優缺點比較、規格對照、各家差異等）。"
    "headers 是欄位名，rows 每一列的長度必須與 headers 相同。沒有適合的內容就回空陣列，不要硬湊。\n"
    "- figures（關鍵數據）：" + FIGURE_RULE
    + "- quiz（自我測驗）：依內容出題幫助主動回想。a 必須是逐字稿裡找得到依據的答案；沒有答案的題目不要出。\n"
    "務求忠於逐字稿，不可自行補充逐字稿沒有講到的知識。" + LANG_RULE + IN_USER
)
NOTES_SECTIONS = {
    "outline": {"label": "章節大綱", "instr": "逐一列出這段講述的「全部」章節，不要精簡、不要只挑重要的。title 為 8～20 字主題；points 為該節重點；anchor 填該節開始處逐字稿中的一句原話（10～20 字，必須真的出現過；anchor 只放原句文字，不含時間與說話者）"},
    "concepts": {"label": "重要概念", "instr": "把這段出現、需要理解的名詞或機制「全部」列出，不要精簡、不要只挑最重要的幾個。term＝名詞；plain＝白話說明；why＝為什麼重要"},
    "tables": {"label": "對照表", "instr": "把這段中「全部」可整理成「多項目 × 多屬性」的內容都還原成表格，不要精簡、不要只做一張。rows 每列長度必須與 headers 相同；沒有就回空陣列，不要硬湊"},
    "figures": {"label": "關鍵數據", "instr": "列出這段裡符合下列標準的「全部」數據，不要精簡。\n" + FIGURE_RULE},
    "quiz": {"label": "自我測驗", "instr": "針對這段的「每一個」重要知識點都出一題，全部列出，不要精簡、不要只出幾題；答案必須在這段裡找得到依據"},
}


def notes_section_schema(section: str) -> dict:
    return {"type": "object", "properties": {section: NOTES_SCHEMA["properties"][section]},
            "required": [section]}


def notes_enhance_prompt(section: str) -> str:
    meta = NOTES_SECTIONS[section]
    return (f"以下是一場研討會／課程逐字稿的其中一段。請{meta['instr']}。"
            "使用與逐字稿相同的主要語言，忠於逐字稿、不可自行補充沒講到的內容。"
            f'這段沒有相關內容就回空陣列。只輸出 JSON {{"{section}":[...]}}。' + LANG_RULE + IN_USER)


# ---------------------------------------------------------------- translate
LANG_LABEL = {"zh": "繁體中文 (Traditional Chinese, Taiwan usage)", "en": "English", "ja": "日本語 (Japanese)"}
TR_SEG_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {"type": "array", "items": {
            "type": "object",
            "properties": {"speaker": {"type": "string"}, "text": {"type": "string"}},
            "required": ["speaker", "text"]}},
    },
    "required": ["segments"],
}


def translate_prompt(lang: str) -> str:
    label = LANG_LABEL.get(lang, lang)
    p = (
        "You are a professional meeting-notes translator. The source text may MIX several languages (e.g. Chinese, English, Japanese). "
        f"Translate EVERYTHING in the JSON given in the user message into {label}, so the entire output is uniformly in {label} (translate the parts that are already in another language too). "
        'Translate speaker labels too (e.g. "說話者1" → "Speaker 1" / "話者1"). '
        "Keep the EXACT same JSON structure, the same array lengths and order — translate values only; do NOT add, remove, merge or reorder items. "
        f'Keep any "[DRI: ...]" tag; render "問：/答：" as the {label} equivalent (e.g. "Q:/A:"). Output JSON only.'
    )
    if lang == "zh":
        p += LANG_RULE
    return p


# =====================================================================================
# 董事長版品質（2026-10-06 spec）：有依據才能寫。
# Every item carries src [{t: seconds, quote}] so server/verify.py can check it against the
# transcript. The transcript reaches Claude as lines "[HH:MM:SS] 說話者: 內容" (ai.transcript_lines).
# =====================================================================================

SRC_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"t": {"type": "number"}, "quote": {"type": "string"}},
        "required": ["t", "quote"],
    },
}
ITEM_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "group": {"type": "string"}, "src": SRC_SCHEMA},
    "required": ["text", "src"],
}


def _items_of(keys) -> dict:
    return {"type": "object", "properties": {k: {"type": "array", "items": ITEM_SCHEMA} for k in keys},
            "required": list(keys)}


# Verbatim rule list (spec §三 / plan Task B) followed by concrete allowed / forbidden examples.
GROUNDING_RULES = (
    "【有依據才能寫：硬規則（違反任何一條，該條就是錯的）】\n"
    "1. 只能寫逐字稿裡有的內容。\n"
    "2. 不可補人名全名、職稱、機構、數字。\n"
    "3. 講者的但書、警告（不要、小心、注意、不建議）必須保留在同一條。\n"
    "4. 講者口誤或前後矛盾照實寫「講者原話為…，依上下文疑為…」。\n"
    "5. 數字照原文單位，不可自行換算。\n"
    "6. 每條附 1–3 個 src（t 為秒、quote 為逐字稿原句 6–40 字）。\n"
    "\n"
    "【規則對照範例】\n"
    "- 人名：逐字稿只出現「許會」「許會計師」→ 寫「許會計師」。✗「許崑源會計師」（全名逐字稿沒有出現）。\n"
    "- 人名不可憑常識「更正」：逐字稿寫「王文曹」→ 照寫「王文曹」。✗ 改成「王文洋」「王文淵」（你猜的名字）。"
    "知名公眾人物的人名聽錯也一樣照逐字稿寫：更正交給校對清單由人確認，條目裡不可自己改。\n"
    "- 職稱／機構：逐字稿「宋秀玲宋署長」→ 寫「宋秀玲署長」。✗「前財政部賦稅署署長宋秀玲」（「前」「財政部賦稅署」是自己補的）。\n"
    "- 口誤：逐字稿「我繳一億的稅，如果對砍，只要繳五千塊」→ ✓「講者原話為『只要繳五千塊』，依上下文（稅額減半）疑為五千萬」。"
    "✗「應納稅額由 1 億元降至約 5000 元」（把口誤當事實）；✗「降至 5000 萬元」（默默改掉、沒有標示）。\n"
    "- 前後矛盾：講者前後都說 183 天，其中一次說「133 天」→ 照第 4 條標示，不可自行統一。\n"
    "- 數字寫法：可以把中文數字改成阿拉伯數字（五千萬 → 5000萬、百分之二十八 → 28%）。"
    "✗ 換算單位（五千萬 → 0.5億）；✗ 補上沒說的幣別（逐字稿只說「五千萬」→ ✗「5000萬美元」）。\n"
    "- 計算：講者沒有說出的結果不可自己算出來寫進去。✗「12%×20%＝4.8%」（講者沒講這個結果，而且算錯）。"
    "講者自己講的算式照寫；講者算錯 → 依第 4 條標示。\n"
    "- 但書：逐字稿「可以除戶變成非稅務居民，但不要強推，這只適合無根台商」→ ✓ 條件與警告寫在同一條。"
    "✗ 只寫「可協助客戶轉為非稅務居民以節稅」（警告被刪掉，意思變成鼓勵）。\n"
    "- 語氣：講者說「應該不會查」→ 寫「講者認為應該不會查」。✗「不會查」（把推測寫成定論）；否定句不可寫成肯定句。\n"
    "- 舉例與假設：講者說「假設你有 6 間豪宅…」→ 寫「講者假設的例子：…」。✗ 寫成真實案例或一般通則。\n"
    "- 聽錯字：逐字稿明顯是同音錯字的一般名詞（「鼓勵所得」＝股利所得）→ text 寫正確的詞，quote 照逐字稿原樣抄（含錯字）。"
    "人名、公司名不適用這條，照逐字稿寫。\n"
    "- 不確定：不確定就不寫。✗ 加「（推測）」來填補；✗ 用你的專業知識補上講者沒講的理由、法條或背景。\n"
    "- 張冠李戴：數字、人名、結論只能掛在講者原本講的那個案例或對象上。"
    "✗ 講者在 A 案例說「匯回 2000 萬以上會被盯上」，卻寫成 B 案例的門檻；"
    "✗ 講者說「倫敦、紐約不跟台灣交換資訊，新加坡要等它們先交換」，卻寫成「新加坡不與英美交換」（主詞與對象被調換）。"
    "✓ 寫之前先確認：這個數字是誰的、哪個案例的、在講什麼。\n"
    "- 遺漏條件：門檻、期間、比例、適用對象一定要跟著結論寫在同一條。"
    "✓「除戶後 2 年內回國恢復戶籍可立即加保，超過 2 年須滿 6 個月」；✗「除戶後回國可立即加保」（少了「2 年內」）。"
    "✓「有戶籍者一年在台滿 31 天且生活重心在台即為稅務居民」；✗「有戶籍就是稅務居民」（少了「滿 31 天」）。"
    "✓「投資公司賣股要課 12% 最低稅負（持股滿 3 年減半）」；✗ 漏掉持有年限條件。\n"
    "\n"
    "【src 的寫法】\n"
    "- quote：從逐字稿「同一行」裡逐字複製連續的 6–40 個字（標點可以省略）。✗ 改寫、✗ 摘要、✗ 把兩處拼在一起、✗ 加字。\n"
    "- t：該 quote 所在那一行開頭的時間換算成秒。例：[00:22:58] → 1378；[01:03:27] → 3807。\n"
    "- 選最能證明這一條的原句：數字、人名出現在哪一句，就引用那一句。\n"
    "\n"
    "【用字】text 一律繁體中文（台灣用語），「臺」統一寫成「台」；quote 照逐字稿原樣，不改字。\n"
)

# ---------------------------------------------------------------- mode detect
MODE_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": ["lecture", "meeting"]}, "reason": {"type": "string"}},
    "required": ["mode", "reason"],
}
MODE_DETECT_PROMPT = (
    "判斷這份逐字稿是「講座」(lecture) 還是「會議」(meeting)。\n"
    "- lecture：一位主講者佔大部分發言，單向講授（課程、演講、說明會、研討會、簡報、培訓）；"
    "其他人只有零星提問或附和。講者自問自答（「那怎麼辦呢？我們來看…」）不算別人發言。\n"
    "- meeting：多人輪流發言、互相討論，會出現決議、分工、時程或待辦（例行會議、專案討論、客戶洽談、訪談）。\n"
    "- 注意：說話者標籤可能不準（同一個人被拆成兩個標籤），要看內容是不是同一個人在連續講課。\n"
    "- 真的判斷不出來 → meeting。\n"
    "reason：用一句繁體中文（30 字內）說明依據，例如「同一位會計師講授稅務規劃約 85 分鐘，聽眾僅零星發言」。\n"
    '只輸出 JSON {"mode":"lecture|meeting","reason":"…"}。' + LANG_RULE + IN_USER
)

# ---------------------------------------------------------------- summary (meeting mode)
MEETING_KEYS = ("actionItems", "mainPoints", "qa")
SUMMARY_MEETING_SCHEMA = _items_of(MEETING_KEYS)
SUMMARY_MEETING_PROMPT = (
    "你是嚴謹的會議記錄員，這份記錄要交給董事長，不能有任何一句沒有依據。"
    "以下是一場「會議」的逐字稿（每行格式「[時:分:秒] 說話者: 內容」）。請整理成三個區塊，每一條都是 "
    '{"text":"…","group":"…","src":[{"t":秒數,"quote":"逐字稿原句"}]}：\n'
    "\n"
    "■ actionItems（待辦）：只收逐字稿中「有人明確承諾要做」或「被明確指派」的事。\n"
    "  - text 格式「事項內容 [DRI: 負責人]」；負責人用逐字稿中的稱呼或說話者標籤，例：「[DRI: 王經理]」「[DRI: 說話者2]」。\n"
    "  - ✓ 收：「我下週五前把報價寄給你」「這個就交給小陳去跟廠商確認」。\n"
    "  - ✗ 不收：AI 自己覺得應該做的建議；「可以考慮」「之後再看看」這類沒有承諾的話；判斷不出誰負責的事"
    "（不要寫「待指派」來湊數）。沒有就回空陣列。\n"
    "■ mainPoints（重點）：最多 15 條。\n"
    "  - text 格式「標題：說明」，標題是 4～14 字的名詞短語，接全形冒號再寫說明；說明保留具體資訊（數字、日期、結論、條件）。\n"
    "  - group：主題分組名稱（4～10 字），同一主題用「完全相同」的 group；同組內依發言時間排序。\n"
    "  - 同一個論點只寫一條；不同議題分開寫。決議、結論要寫出是誰的意見、是否已拍板。\n"
    "■ qa（問答）：只收「一個人提問、另一個人回答」的真實問答。text 格式「問：…\\n答：…」。\n"
    "  - ✗ 不收：講者自問自答的設問；議程、設備、寒暄（「聽得到嗎」「要不要休息」）；沒有得到回答的問題。\n"
    "  - 答案照回答者的原意寫，回答是「還不確定／再確認」也要照寫。沒有就回空陣列。\n"
    "\n"
    "同一件事只放在最適合的一個區塊，不要在 mainPoints 和 qa 重複。\n\n"
    + GROUNDING_RULES
    + '\n只輸出 JSON {"actionItems":[…],"mainPoints":[…],"qa":[…]}。' + LANG_RULE + IN_USER
)

# ---------------------------------------------------------------- summary (lecture mode)
LECTURE_KEYS = ("conclusions", "advice", "risks", "cases", "mainPoints", "qa")
SUMMARY_LECTURE_SCHEMA = _items_of(LECTURE_KEYS)
SUMMARY_LECTURE_PROMPT = (
    "你是嚴謹的講座記錄員，這份記錄要交給董事長，不能有任何一句沒有依據。"
    "以下是一場「講座」（單一主講者授課／演講）的逐字稿（每行格式「[時:分:秒] 說話者: 內容」）。"
    "講座沒有待辦事項：不要產出待辦，也不要替聽眾編出「應該做什麼」。請整理成六個區塊，每一條都是 "
    '{"text":"…","group":"…","src":[{"t":秒數,"quote":"逐字稿原句"}]}：\n'
    "\n"
    "■ conclusions（核心結論）：3～7 條，講者明確表達的主要結論或主張。用講者的立場寫（「講者認為…」「講者指出…」）。\n"
    "■ advice（講者建議，含但書）：講者建議聽眾怎麼做。條件、適用對象、但書必須寫在同一條。\n"
    "  - ✓「講者建議：無根台商可考慮除戶成為非稅務居民；但不要強推，且須有合理商業理由，否則會被實質課稅原則推翻」。\n"
    "  - ✗「建議協助客戶轉為非稅務居民」（缺了適用對象與警告）。\n"
    "■ risks（風險提醒）：講者提醒的風險、警告、不可以做的事、會被查或被罰的情況。"
    "原話有「不要」「小心」「注意」「不建議」「會被追稅」的都要收。\n"
    "■ cases（案例）：講者舉的案例。text 開頭寫明是哪一種：「講者提到的案例：…」或「講者假設的情境：…」；"
    "案例中的人名、公司名照逐字稿寫法，不補全名、不補職稱。\n"
    "■ mainPoints（重點）：最多 25 條，涵蓋上面四區沒有收的其他重要內容（制度說明、規則、數據）。\n"
    "  - text 格式「標題：說明」，標題是 4～14 字的名詞短語；說明保留具體資訊（數字、稅率、年份、條件）。\n"
    "  - group：主題分組名稱（4～10 字，例如「稅務居民認定」「境外公司與CFC」），同一主題用「完全相同」的 group；"
    "同組內依講述時間排序。\n"
    "■ qa（問答）：只收「聽眾真的開口提問、講者回答」的問答。text 格式「問：…\\n答：…」。\n"
    "  - ✗ 不收：講者自問自答的設問（「那要怎麼辦呢？我告訴你…」）；講者問台下「對不對」「瞭解嗎」；沒有回答的問題。\n"
    "  - 說話者標籤可能不準：只有內容確實是另一個人在發問，才算問答。沒有就回空陣列（講座常常沒有）。\n"
    "\n"
    "每一件事只放在最適合的一個區塊（警告放 risks、建議放 advice、例子放 cases），不要在多個區塊重複。\n\n"
    + GROUNDING_RULES
    + '\n只輸出 JSON {"conclusions":[…],"advice":[…],"risks":[…],"cases":[…],"mainPoints":[…],"qa":[…]}。'
    + LANG_RULE + IN_USER
)

SUMMARY_MAIN_POINT_CAP = {"lecture": 25, "meeting": 15}

# ---------------------------------------------------------------- public figures (user decision 2026-10-07)
PUBLIC_REASON = "公眾人物，依常識應為「{right}」（{identity}）；請確認"
PUBLIC_FIGURE_RULE = (
    "【知名公眾人物的人名：可以用常識建議，但一律交給人確認】\n"
    "對象只限「知名公眾人物」：政府官員、立法委員／民意代表、上市櫃公司的董事長／創辦人／高階主管、知名企業家、"
    "公家機關或公共機構的首長。逐字稿把這些人的名字聽錯（同音或近音），而上下文（職位、公司、事件）明確指向那個人時，"
    "可以依你的一般常識建議正確寫法，即使正確寫法沒有在逐字稿或【詞庫】出現。這種建議：\n"
    "  - publicFigure 填 true，identity 填身分（例：日月光董事長、內政部長、立委）；\n"
    "  - confidence 最高只能 medium（永遠不會自動套用，一定由人確認）；\n"
    "  - reason 寫成「公眾人物，依常識應為「正確寫法」（身分）；請確認」。\n"
    "  ✓「張前生」→「張虔生」（上下文講日月光的老闆；張虔生是日月光董事長）。\n"
    "  ✓「曹新成」→「曹興誠」（上下文講放棄國籍的知名企業家；曹興誠是聯電創辦人）。\n"
    "  ✓「曾帆成」→「曾繁城」（上下文講台積電副董事長；曾繁城是台積電前副董事長）。\n"
    "  ✓「餘萬如委員」→「余宛如委員」（上下文講推動資金匯回的立委；余宛如是立委）。\n"
    "  ✓「劉思芳」→「劉世芳」（上下文講同期的委員；劉世芳是內政部長、前立委）。\n"
    "  ✗ 一般人（客戶、朋友、親戚、同事、講者本人、沒有公眾身分的人）的名字：不可改成發音相近的名人名字"
    "（例：講者的客戶「陳志明」→ ✗「陳致中」）。一般人名照舊規則：正確寫法要在逐字稿別處或【詞庫】出現過才可以建議。\n"
    "  ✗ 不確定逐字稿講的是不是那位公眾人物（上下文沒有職位、公司、事件可以對上）→ 不要當成公眾人物"
    "（publicFigure false；校對時不列，複核時回答 unsure）。\n"
)


def public_reason(right: str, identity: str = "") -> str:
    """Reason text of a public-figure name correction (always a checklist row for the human)."""
    return PUBLIC_REASON.format(right=right, identity=(identity or "").strip() or "身分請查證")


# ---------------------------------------------------------------- correction (ASR mishearings)
CORRECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "wrong": {"type": "string"},
                    "right": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                    "ts": {"type": "array", "items": {"type": "number"}},
                    "publicFigure": {"type": "boolean"},
                    "identity": {"type": "string"},
                },
                "required": ["wrong", "right", "confidence", "reason", "ts"],
            },
        },
    },
    "required": ["items"],
}
CORRECTION_PROMPT = (
    "你是語音辨識校對員。使用者訊息有【詞庫】（這個領域常用的正確寫法，僅供參考）和【逐字稿】（每行「[時:分:秒] 說話者: 內容」）。"
    "請找出逐字稿中「語音辨識聽錯」的詞，產出「錯字 → 正字」清單。\n"
    "\n"
    "【逐段完整掃描】給你的逐字稿是整場會議的其中一段（前後段另外處理）。請逐行讀完，"
    "這一段裡每一個聽錯的詞都要列出（包含只出現一次的、語助詞中的、人名、機構、金融稅務術語、"
    "口語轉寫錯誤如 鑾高→蠻高、輪交→躉繳），不要只挑重要的。"
    "例：「寶兒大」→「保額大」、「科醫產稅」→「課遺產稅」、「園大」→「元大」（券商名）。"
    "列得多不代表會自動改：沒把握的照下面規則標 medium／low，交給人確認。\n"
    "\n"
    "【只收語音辨識錯誤】發音相同或相近、但字寫錯，而且放在上下文裡意思不通的詞：\n"
    "  ✓ 同音字：「鼓勵所得」→「股利所得」（上下文講分離課稅 28%）；「證與稅」→「贈與稅」；「中俄所得稅」→「綜合所得稅」。\n"
    "  ✓ 縮寫聽錯：「CIS」→「CRS」（上下文講金融帳戶資訊交換）；「OPU」→「OBU」；「EDF」→「ETF」。\n"
    "  ✓ 專有名詞：「揮打」→「輝達」（同一份逐字稿別處有正確的「輝達」）；「維新群島」→「維京群島」。\n"
    "【一律不收】\n"
    "  ✗ 文法、語氣、贅字、標點、口語化的修飾（那個、就是說、齁）。\n"
    "  ✗ 講者自己說錯的內容（口誤、數字講錯、前後矛盾）：那是講者的原話，不是辨識錯誤。\n"
    "  ✗ 你不確定原本是什麼的詞；✗ 寫法不同但意思正確的詞（臺／台、瞭解／了解）。\n"
    "  ✗ 詞庫有、但逐字稿裡沒有對應錯字的詞（不要為了用到詞庫而硬找）。\n"
    "\n"
    "【欄位】\n"
    "- wrong：必須是逐字稿中「實際出現」的字串（逐字複製）。wrong 會被「全文取代」，所以：\n"
    "  若同一寫法在別處是正確用法（例：「鼓勵年輕人」的「鼓勵」是對的），要改用更長、只涵蓋錯誤處的寫法（例：「鼓勵所得」），"
    "做不到就把 confidence 降為 medium。\n"
    "- right：正確寫法，優先採用【詞庫】或逐字稿別處已出現的寫法。\n"
    "- confidence：\n"
    "  high＝發音吻合、上下文只有這一種解讀，而且正確寫法在【詞庫】或逐字稿別處出現過，或是常見詞的明顯同音錯字。"
    "high 會「自動套用」到逐字稿，寧缺勿濫。\n"
    "  medium＝很可能，但需要人聽錄音確認。人名、公司名的正確寫法若沒有在【詞庫】或逐字稿別處出現，最高只能 medium"
    "（一般人名這種情況不收，除非符合下面的公眾人物規則）。\n"
    "  low＝只是推測。\n"
    "  牽涉數字的修正（金額、天數、稅率、年份）一律不可為 high。\n"
    "- reason：一句繁體中文說明依據（例：「上下文講分離課稅 28%，應為股利所得」）。\n"
    "- ts：這個錯字出現的時間（秒），取該行開頭時間換算，例：[00:21:10] → 1270；最多列 5 個。\n"
    "- publicFigure／identity：只有知名公眾人物的人名更正才填 publicFigure true 和 identity（見下方規則）；其他一律 false、空字串。\n"
    "\n"
    + PUBLIC_FIGURE_RULE +
    "\n"
    "同一個錯字只列一筆（列出它的各個時間）。沒有就回空陣列。\n"
    '只輸出 JSON {"items":[{"wrong":"…","right":"…","confidence":"high|medium|low","reason":"…","ts":[秒數],'
    '"publicFigure":false,"identity":""}]}。'
    + LANG_RULE
)


def correction_user_message(transcript_text: str, glossary) -> str:
    """User message for CORRECTION_PROMPT: glossary (list or newline string) + transcript."""
    if isinstance(glossary, str):
        glossary = glossary.splitlines()
    terms = [g.strip() for g in glossary or [] if isinstance(g, str) and g.strip()]
    return f"【詞庫】\n{'、'.join(terms) or '（無）'}\n\n【逐字稿】\n{transcript_text}"


# ---------------------------------------------------------------- speaker slips / contradictions
SLIPS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t": {"type": "number"},
                    "quote": {"type": "string"},
                    "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["t", "quote", "issue", "suggestion"],
            },
        },
    },
    "required": ["items"],
}
SLIPS_PROMPT = (
    "你是校對員。請找出逐字稿中「講者自己講錯或前後矛盾」的地方，交給人工確認。\n"
    "\n"
    "【要找的】只根據這份逐字稿本身就能看出問題的：\n"
    "  ✓ 數字和上下文對不上：「我繳一億的稅，如果對砍，只要繳五千塊」→ 減半應是五千萬。\n"
    "  ✓ 前後矛盾：前後都說「183 天」，其中一次說「133 天」。\n"
    "  ✓ 講者自己的算式算不起來：「所得 60 億，稅率 48，要繳 24 億」→ 60 億×40%＝24 億，48 對不上。\n"
    "  ✓ 同一個人、同一件事前後說法不同（時間、金額、身分、結論相反）。\n"
    "【不要找的】\n"
    "  ✗ 語音辨識的同音錯字（鼓勵所得／股利所得）：那是另一個校正步驟的事。\n"
    "  ✗ 口語贅字、文法、標點。\n"
    "  ✗ 你認為講者說法和現行法規或常識不同：不可用外部知識糾正講者，只看逐字稿內部是否自相矛盾。\n"
    "\n"
    "【欄位】\n"
    "- t：有問題那一行開頭的時間換算成秒（例：[00:22:58] → 1378）。\n"
    "- quote：從該行逐字複製的原句，6–40 字，要包含有問題的那個數字或說法。\n"
    "- issue：一句繁體中文說明哪裡矛盾，並指出對照的依據（例：「前文說稅額一億、稅率減半，減半後應為五千萬，原話卻是五千塊」）。\n"
    "- suggestion：依上下文最可能的正確說法，寫成「依上下文疑為…」；判斷不出就留空字串。\n"
    "寧缺勿濫：只列有明確依據的。沒有就回空陣列。\n"
    '只輸出 JSON {"items":[{"t":秒數,"quote":"…","issue":"…","suggestion":"…"}]}。' + LANG_RULE + IN_USER
)

# ---------------------------------------------------------------- notes with sources
NOTES_SRC_SCHEMA = {
    "type": "object",
    "properties": {
        "outline": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "anchor": {"type": "string"},
                           "points": {"type": "array", "items": {"type": "string"}}, "src": SRC_SCHEMA},
            "required": ["title", "points", "src"]}},
        "concepts": {"type": "array", "items": {
            "type": "object",
            "properties": {"term": {"type": "string"}, "plain": {"type": "string"}, "why": {"type": "string"},
                           "src": SRC_SCHEMA},
            "required": ["term", "plain", "src"]}},
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"},
                           "headers": {"type": "array", "items": {"type": "string"}},
                           "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                           "src": SRC_SCHEMA},
            "required": ["title", "headers", "rows", "src"]}},
        "figures": {"type": "array", "items": {
            "type": "object",
            "properties": {"group": {"type": "string"}, "label": {"type": "string"}, "value": {"type": "string"},
                           "src": SRC_SCHEMA},
            "required": ["label", "value", "src"]}},
        "quiz": {"type": "array", "items": {
            "type": "object",
            "properties": {"q": {"type": "string"}, "a": {"type": "string"}, "src": SRC_SCHEMA},
            "required": ["q", "a", "src"]}},
    },
    "required": ["outline", "concepts", "tables", "figures", "quiz"],
}
NOTES_SRC_RULES = (
    "【每一項都要附 src】outline 的每一節、concepts、tables、figures、quiz 的每一筆都要有 src（1–3 個）：\n"
    "- outline：src 引用這一節最關鍵的 1–3 句（points 裡的數字、人名出自哪句就引用哪句）。\n"
    "- figures：src 必須引用「說出這個數字」的那一句；value 照講者原話的數字與單位（可把中文數字寫成阿拉伯數字，不可換算）。"
    "label 只能用逐字稿裡的說法描述這個數字，✗ 自己加「首次／後續」「預估」之類逐字稿沒有的分類。"
    "講者舉例或假設的數字，label 要註明「（講者舉例）」。\n"
    "- quiz：答案必須能在 src 引用的原句找到依據；✗ 在題目或答案裡用引號引用逐字稿的錯字（例：「淫娛證券化」應寫成盈餘證券化）。\n"
    "- tables：src 引用表格內容出處的原句；每一格都要有逐字稿依據，沒有依據的格子寫「（未提及）」，不可自己填。\n"
    "- concepts：plain／why 只能用講者的解釋改寫，✗ 用你自己的專業知識補充講者沒講的定義或背景。\n"
)
_CONCEPT_OLD = "term＝名詞本身；plain＝用白話說明它是什麼；why＝為什麼重要／用在哪裡。"
_CONCEPT_NEW = ("只收講者有解釋的名詞。term＝名詞本身；plain＝用白話改寫「講者自己的解釋」；"
                "why＝講者說它為什麼重要（講者沒說就留空字串）。✗ 用你自己的知識補定義。")
assert _CONCEPT_OLD in NOTES_PROMPT
NOTES_SRC_PROMPT = (NOTES_PROMPT.replace(LANG_RULE + IN_USER, "").replace(_CONCEPT_OLD, _CONCEPT_NEW)
                    + "\n\n" + NOTES_SRC_RULES + "\n" + GROUNDING_RULES + LANG_RULE + IN_USER)

# section instructions for the src-aware notes enhance: no 「全部／不要精簡」 pressure (it pushes the
# model to pad with unsupported items); only what the speaker actually said.
NOTES_SRC_SECTIONS = {
    "outline": {"label": "章節大綱", "instr": (
        "依講述順序列出這段的章節。title 為 8～20 字主題；points 為該節講者講到的重點；"
        "anchor 填該節開始處逐字稿中的一句原話（10～20 字，必須真的出現過；只放原句文字，不含時間與說話者）")},
    "concepts": {"label": "重要概念", "instr": (
        "列出這段中講者有解釋的名詞或機制。term＝名詞；plain＝用白話改寫「講者自己的解釋」"
        "（講者沒有解釋的名詞不要列）；why＝講者說它為什麼重要（講者沒說就留空字串）。✗ 用你自己的知識補定義")},
    "tables": {"label": "對照表", "instr": (
        "把這段中講者明確比較的「多項目 × 多屬性」內容還原成表格。rows 每列長度必須與 headers 相同；"
        "沒有這類內容就回空陣列，不要硬湊")},
    "figures": {"label": "關鍵數據", "instr": "列出這段裡符合下列標準的數據。\n" + FIGURE_RULE},
    "quiz": {"label": "自我測驗", "instr": (
        "針對這段講者講清楚的知識點出題；答案必須在這段裡找得到依據，沒有明確答案的不要出")},
}


def notes_src_section_schema(section: str) -> dict:
    return {"type": "object", "properties": {section: NOTES_SRC_SCHEMA["properties"][section]},
            "required": [section]}


def notes_src_enhance_prompt(section: str) -> str:
    meta = NOTES_SRC_SECTIONS[section]
    return (f"以下是一場研討會／課程逐字稿的其中一段（每行「[時:分:秒] 說話者: 內容」）。請{meta['instr']}。"
            "使用與逐字稿相同的主要語言，忠於逐字稿、不可自行補充沒講到的內容。"
            f"這段沒有相關內容就回空陣列。\n\n{NOTES_SRC_RULES}\n{GROUNDING_RULES}"
            f'只輸出 JSON {{"{section}":[...]}}。' + LANG_RULE + IN_USER)


# ---------------------------------------------------------------- enhance one summary section with sources
SECTION_META_SRC = {
    "actionItems": {"label": "待辦", "instr": (
        "逐條列出逐字稿中「有人明確承諾要做」或「被明確指派」的事，text 格式「事項內容 [DRI: 負責人]」，"
        "負責人用逐字稿中的稱呼或說話者標籤；AI 自己的建議、沒有承諾的「可以考慮」、判斷不出負責人的事都不列")},
    "mainPoints": {"label": "重點", "instr": (
        "逐條列出這段的重要重點，text 格式「標題：說明」（標題 4～14 字名詞短語，接全形冒號），"
        "group 填主題分組名稱（4～10 字），說明保留具體數字、條件、結論")},
    "qa": {"label": "問答", "instr": (
        "列出這段中「一個人提問、另一個人回答」的真實問答，text 格式「問：…\\n答：…」；"
        "講者自問自答的設問、議程與設備確認、沒有回答的問題都不列")},
    "conclusions": {"label": "核心結論", "instr": "列出講者在這段明確表達的結論或主張，用講者的立場寫（「講者認為…」）"},
    "advice": {"label": "講者建議", "instr": "列出講者在這段給聽眾的建議，條件、適用對象、但書必須寫在同一條"},
    "risks": {"label": "風險提醒", "instr": (
        "列出講者在這段提醒的風險、警告、不可以做的事；原話有「不要」「小心」「注意」「不建議」「會被追稅」的都要收")},
    "cases": {"label": "案例", "instr": (
        "列出講者在這段舉的案例，text 開頭寫明「講者提到的案例：」或「講者假設的情境：」，人名照逐字稿寫法")},
}
ITEMS_SRC_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": ITEM_SCHEMA}},
                    "required": ["items"]}
POLISH_SRC_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "group": {"type": "string"},
                    "src": {"type": "array", "items": {"type": "integer"}},
                    "drop": {"type": "boolean"},
                },
                "required": ["text", "src"],
            },
        },
    },
    "required": ["items"],
}


def enhance_src_prompt(section: str) -> str:
    """Chunk extraction for one summary section (meeting or lecture key); Items with src."""
    meta = SECTION_META_SRC[section]
    return ("以下是一段逐字稿（每行「[時:分:秒] 說話者: 內容」）。"
            f"請{meta['instr']}。這段沒有相關內容就回空陣列。每一條都是"
            '{"text":"…","group":"…","src":[{"t":秒數,"quote":"逐字稿原句"}]}。\n\n'
            + GROUNDING_RULES + '只輸出 JSON {"items":[...]}。' + LANG_RULE + IN_USER)


def polish_src_prompt(section: str, n: int) -> str:
    """Merge/polish pass over numbered raw items; output src = raw item numbers (code unions their src)."""
    meta = SECTION_META_SRC[section]
    return (
        f"以下是從逐字稿分批擷取的「{meta['label']}」清單，共 {n} 條（已編號，每條附有逐字稿原句）。請整理成正式記錄：\n"
        "- 改寫成精簡書面語，刪除口語贅字；數字、單位、人名、條件、但書、講者語氣（「應該」「可能」）一律保留原樣。\n"
        "- 只有記錄「同一件事」的條目才合併；不同的事即使主題相近也各自保留。\n"
        f"- 每條輸出在 src 列出它涵蓋的原始編號；{n} 條原始編號每一條都要被涵蓋或標 drop。\n"
        "- 只能使用清單裡已有的資訊：✗ 加入新的事實、人名全名、職稱、數字或理由；"
        "原始條目裡的「講者原話為…，依上下文疑為…」標示必須保留。\n"
        "- 完全重複、或只是議程／設備／寒暄的條目，標 drop:true（text 留空字串）。\n"
        "- group：主題分組名稱（4～10 字），同一主題用完全相同的名稱。\n"
        '只輸出 JSON {"items":[{"text":"...","group":"...","src":[編號],"drop":false}]}。' + LANG_RULE
        + "\n\n（清單在使用者訊息中。）"
    )


# Plan names (aliases) — the prompt set Task C wires into the pipeline.
SUMMARY_MEETING = SUMMARY_MEETING_PROMPT
SUMMARY_LECTURE = SUMMARY_LECTURE_PROMPT
MODE_DETECT = MODE_DETECT_PROMPT
CORRECTION = CORRECTION_PROMPT
NOTES = NOTES_SRC_PROMPT
SLIPS = SLIPS_PROMPT


# ---------------------------------------------------------------- adjudicate (word-level review cards)
ADJUDICATE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "span": {"type": "string"},
                    "action": {"type": "string", "enum": ["replace", "keep", "delete"]},
                    "suggestion": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "itemIds": {"type": "array", "items": {"type": "string"}},
                    "fromFlags": {"type": "array", "items": {"type": "string"}},
                    "publicFigure": {"type": "boolean"},
                    "identity": {"type": "string"},
                },
                "required": ["span", "action", "suggestion", "reason", "confidence", "itemIds", "fromFlags"],
            },
        },
    },
    "required": ["items"],
}
ADJUDICATE_PROMPT = (
    "你是資深的會議紀錄審稿人，負責在紀錄交給董事長之前，做最後一道「逐字對照逐字稿」的審稿。"
    "你的產出會變成一張張「單字訂正卡」：使用者只看一小段上下文、你的建議和一句理由，按一下就套用。"
    "所以每張卡都必須精準、有依據、可以直接套用。\n"
    "\n"
    "【你會收到】\n"
    "1. 人名與異寫清單：程式從條目與逐字稿整理出的人名，以及逐字稿裡相近的寫法、次數、時間。\n"
    "2. 名詞校正紀錄：已自動套用或待確認的「聽錯字→正字」。\n"
    "3. 程式旗標 [F:編號]：程式驗證（名稱查無、數字查無、原句查無、算式不符）、待確認的聽錯字（term）、講者口誤（slip）。"
    "每個旗標都要判斷，並在對應卡片的 fromFlags 填上它的編號。\n"
    "4. 摘要／筆記條目 [I:編號]：條目全文、引用原句，以及原句前後約 60 秒的逐字稿。\n"
    "\n"
    "【你要做的】\n"
    "A. 逐一判斷每個程式旗標：是真的錯（給修正）、還是誤報（action keep，說明為什麼沒問題）。\n"
    "B. 逐條審每個條目，對照它的原句與前後逐字稿，主動找出程式抓不到的錯：\n"
    "   - 人名異寫：條目用了逐字稿裡較少見或明顯聽錯的寫法，而逐字稿別處有正確寫法。\n"
    "   - 隱含的數字矛盾：條目裡的數字彼此算不起來（基數×比率≠結果），或和逐字稿原話不同。\n"
    "   - 曲解：把講者沒有矛盾的話說成矛盾、把推測寫成定論、主詞或對象張冠李戴、遺漏關鍵條件。\n"
    "C. 每個問題輸出一張卡，只處理「一個詞或一小段」，不要整句重寫。\n"
    "\n"
    "【欄位】\n"
    "- span：要處理的原文片段，必須從「條目原文」或「逐字稿」逐字複製（含標點、全形半形一模一樣），否則整張卡會被丟棄。"
    "取最短但不會誤傷的長度：套用時會替換「逐字稿全部出現處」和「所有含這段文字的條目」，"
    "所以太短的片段（例如單獨的「48」「中」）要帶上前後一兩個字，讓它只命中真正要改的地方。\n"
    "- action：replace＝改成 suggestion；keep＝原文正確或無法判斷，保留；delete＝從條目刪掉這段（只刪條目，不刪逐字稿），"
    "用於條目裡多餘、捏造或誤導的片段。\n"
    "- suggestion：replace 時的新文字（直接取代 span，前後要能接得上）；keep、delete 時填空字串。\n"
    "- reason：繁體中文一句話，60 字內，必須包含逐字稿的時間 [HH:MM:SS] 和原話（用「」引起來）作為依據。\n"
    "- confidence：high＝逐字稿有明確依據；medium＝依據充分但需要人看一眼；low＝不確定。\n"
    "- itemIds：span 出現在哪些條目，填條目編號（[I:…] 裡的 8 碼）；只在逐字稿裡的填空陣列。\n"
    "- fromFlags：這張卡回應了哪些程式旗標（[F:…] 裡的 8 碼）；你自己發現的問題填空陣列。\n"
    "- publicFigure／identity：知名公眾人物的人名更正（見下方公眾人物規則）填 true 和身分；其他一律 false、空字串。\n"
    "\n"
    "【硬規則】\n"
    "1. span 必須是給你的文字中真實存在的字串，逐字複製，不可改寫、不可拼接兩處。\n"
    "2. suggestion 必須以逐字稿為依據，並在 reason 引用時間與原話。逐字稿別處有正確寫法 → 用那個寫法。\n"
    "3. 不可捏造事實：不可用你的常識補人名、職稱、法條、數字。逐字稿裡找不到正確寫法的人名 → keep、confidence low，"
    "reason 寫「逐字稿只有○○寫法，真名請查證」。唯一例外是知名公眾人物的人名聽錯（見下方規則）：可以出 replace 卡，"
    "publicFigure true、confidence medium，reason 用規定的格式；標著「公眾人物」的待確認聽錯字（term）也照這樣出 replace 卡，"
    "除非上下文明確顯示講的不是那個人。\n"
    "4. 不確定 → action keep、confidence low，reason 寫要查什麼（例：「請回聽 [01:10:02] 稅率是 40 還是 48」）。\n"
    "5. 同一個 span 改成同一個 suggestion 只出一張卡（把所有相關條目放進 itemIds、所有相關旗標放進 fromFlags）。\n"
    "6. 程式旗標不可略過：每個 [F:…] 都要出現在某一張卡的 fromFlags 裡。誤報就出 keep 卡說明理由。\n"
    "7. 講者口誤（slip）：條目已經照實揭露（「講者原話為…，疑為…」）→ keep；條目把口誤當事實寫 → replace 成揭露的寫法；"
    "slip 本身是誤判（其實不矛盾）→ keep 並說明，不可順著錯的 slip 去改條目。\n"
    "8. 待確認的聽錯字（term）：span＝聽錯的字，suggestion＝正字；判斷是誤報（原字是正確用法）→ keep。\n"
    "\n"
    "【範例】\n"
    "✓ 人名異寫，逐字稿別處有正確寫法：條目寫「黃任宗」，人名清單顯示逐字稿 [01:10:02] 有「黃任中怎麼死的」→ "
    '{"span":"黃任宗","action":"replace","suggestion":"黃任中","reason":"[01:10:02] 逐字稿有「黃任中怎麼死的」，其餘「黃任宗」為同音誤聽",'
    '"confidence":"high","itemIds":["e5583796"],"fromFlags":[]}\n'
    "✓ 隱含數字矛盾：條目寫「以當時48%的股利所得課稅要繳24億」，但 60 億×48%＝28.8 億≠24 億；逐字稿 [01:10:02] 原話「股利所得48他要繳24億的稅」→ "
    '{"span":"以當時48%的股利所得課稅要繳24億","action":"replace","suggestion":"若按當時股利所得課稅要繳24億（講者原話稅率為「48」，60億×48%≠24億，疑為40%）",'
    '"reason":"[01:10:02] 原話「股利所得48他要繳24億的稅」，60億×48%＝28.8億，與24億不符","confidence":"medium","itemIds":["e5583796"],"fromFlags":["…"]}\n'
    "✓ 總額與部分不是矛盾：條目寫「前後數字不一致」，逐字稿 [00:15:10]「當年度個人海外租稅天堂公司報稅50億，絕大部分是我幫忙報的」、"
    "[01:15:41]「我一報就40億」→ 50 億是當年度總額、40 億是其中一位客戶，兩者不矛盾。"
    "對 slip 旗標出 keep 卡（reason 說明是總額與部分）；條目裡的「，前後數字不一致」是曲解 → delete 那一段。✗ 不可建議把 40 億改成 50 億。\n"
    "✓ 忠實改寫不算捏造：程式旗標說「舊制不動產」在逐字稿裡沒有出現，但逐字稿 [00:25:49] 是「舊字不動產,外國人比較有利」（「舊字」是「舊制」的同音誤聽），條目只是忠實重述 → "
    '{"span":"舊制不動產","action":"keep","suggestion":"","reason":"[00:25:49] 原話「舊字不動產」為「舊制」誤聽，條目忠實重述","confidence":"high","itemIds":["a2f89dea"],"fromFlags":["…"]}\n'
    "✗ 錯誤示範：span 寫成條目裡沒有的字（例如把「黃任宗（逐字稿另寫作黃任中）」縮寫成「黃任宗另寫作黃任中」）；"
    "suggestion 用常識補全名（逐字稿只有「李明基司長」，卻建議改成別的名字）；reason 沒有時間和原話；同一個 span 出好幾張相同的卡。\n"
    "\n"
    "\n"
    + PUBLIC_FIGURE_RULE +
    "\n"
    "沒有問題的條目不用出卡。寧可少而準，每張卡都要讓使用者按「照建議」就對。\n"
    '只輸出 JSON {"items":[{"span":"…","action":"replace|keep|delete","suggestion":"…","reason":"…","confidence":"high|medium|low","itemIds":[…],"fromFlags":[…],'
    '"publicFigure":false,"identity":""}]}。'
    + LANG_RULE + "\n\n（審稿資料在使用者訊息中。）"
)
ADJUDICATE = ADJUDICATE_PROMPT


# ---------------------------------------------------------------- verify candidates (second opinion)
VERIFY_CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["confirm", "reject", "unsure"]},
                    "reason": {"type": "string"},
                    "publicFigure": {"type": "boolean"},
                    "identity": {"type": "string"},
                },
                "required": ["id", "decision", "reason"],
            },
        },
    },
    "required": ["items"],
}
VERIFY_CANDIDATES_PROMPT = (
    "你是嚴格的語音辨識複核員，負責「第二意見」。前一位校對員逐段掃描逐字稿，列出了一批「可能聽錯的字」（錯字 → 正字），"
    "其中大多是誤報。你的工作是逐筆複核，只放行「確定是語音辨識聽錯」的候選；被你否決的候選不會給使用者看。\n"
    "\n"
    "【你會收到】\n"
    "1. 【詞庫】：這個領域常用的正確寫法（僅供參考，不代表逐字稿一定要用到）。\n"
    "2. 【候選】：每筆有編號 [C:編號]、「錯字」→「正字」、初判信心與理由，以及錯字在逐字稿出現的位置"
    "（最多 3 處，「[時:分:秒] …前文【錯字】後文…」）。\n"
    "\n"
    "【逐筆判斷，decision 三選一】\n"
    "- confirm：明確是語音辨識聽錯，而且「正字」就是講者實際說的話。必須同時成立：\n"
    "  (1) 錯字與正字發音相同或相近；(2) 錯字放在上下文裡意思不通或明顯不合情境；"
    "(3) 換成正字後上下文通順，而且上下文（或【詞庫】、逐字稿別處的正確寫法）支持這個正字。\n"
    "- reject：以下任一情況就否決：\n"
    "  · 錯字其實是正確的、或照講者原話也說得通（只是不常見的說法、口語、方言、俚語）；\n"
    "  · 正字會改變講者原意，或只是你「覺得比較好」的另一種說法；\n"
    "  · 只是文法、語氣、贅字、語助詞、標點、用字風格、寫法差異（臺／台、瞭解／了解、蠻／滿）；\n"
    "  · 把一個說得通的中文詞換成另一個也說得通的詞，而上下文沒有明確支持；\n"
    "  · 一般人名、公司名：除非同一個正確寫法在逐字稿別處（或【詞庫】）出現過，否則一律 reject"
    "（知名公眾人物例外，見下方規則）；\n"
    "  · 講者自己的口誤、數字講錯：那是原話，不是辨識錯誤。\n"
    "- unsure：可能是聽錯，但上下文不足以判斷（需要人回聽錄音）。不要把 unsure 當成「懶得判斷」：能判斷就判斷。\n"
    "\n"
    "【範例】\n"
    "✓ confirm「園大」→「元大」：開場跟元大的人打招呼「我們在園大開戶」，元大是券商／銀行名，「園大」不是任何機構。\n"
    "✓ confirm「輪交」→「躉繳」：上下文講保單一次繳清保費，「輪交保費」不通，躉繳是保險術語。\n"
    "✓ confirm「寶兒大」→「保額大」：上下文講保單「保費小、寶兒大」，保額是保險術語，「寶兒」不通。\n"
    "✗ reject 語助詞或口語的改動：「好啊」→「好」、「對嘛」→「對嗎」、「這樣齁」→「這樣喔」。\n"
    "✗ reject 用字風格：「蠻高」→「滿高」、「台灣」→「臺灣」。\n"
    "✗ reject 沒有上下文支持的換詞：「他很認真」→「他很任真」或「規劃」→「規畫」這類原文本來就通的句子。\n"
    "✗ reject 一般人名：「王小明」→「王曉明」，逐字稿別處沒有「王曉明」，也不是公眾人物。\n"
    "\n"
    + PUBLIC_FIGURE_RULE +
    "  複核時：符合公眾人物規則的人名候選 → confirm（上下文不夠明確就 unsure），publicFigure true、identity 填身分，"
    "reason 用規定格式；不可只因為正確寫法沒有在逐字稿或【詞庫】出現就 reject。"
    "只有上下文明確顯示講的不是那位公眾人物（例如是講者的客戶、親戚）才 reject，此時 publicFigure 填 false。\n"
    "\n"
    "【欄位】\n"
    "- id：候選編號（[C:…] 裡的字串，原樣照抄）。每一筆候選都要回答，不可略過。\n"
    "- decision：confirm｜reject｜unsure。\n"
    "- reason：繁體中文一句話（40 字內），說明依據（引用上下文的關鍵字）。\n"
    "- publicFigure／identity：知名公眾人物的人名更正填 true 和身分；其他一律 false、空字串。\n"
    "\n"
    "寧可少放行，也不要放行誤報：沒有把握就 unsure，明顯不是聽錯就 reject。\n"
    '只輸出 JSON {"items":[{"id":"…","decision":"confirm|reject|unsure","reason":"…","publicFigure":false,"identity":""}]}。'
    + LANG_RULE + "\n\n（候選資料在使用者訊息中。）"
)
VERIFY_CANDIDATES = VERIFY_CANDIDATES_PROMPT


def verify_candidates_user_message(cands: list[dict], glossary) -> str:
    """cands: [{id, wrong, right, confidence, reason, contexts: [str]}] -> VERIFY_CANDIDATES user message."""
    if isinstance(glossary, str):
        glossary = glossary.splitlines()
    terms = [g.strip() for g in glossary or [] if isinstance(g, str) and g.strip()]
    blocks = []
    for c in cands:
        why = f"；初判理由：{c['reason']}" if c.get("reason") else ""
        pf = f"；初判為公眾人物：{c.get('identity') or '身分未填'}" if c.get("publicFigure") else ""
        b = [f"[C:{c['id']}] 「{c['wrong']}」→「{c['right']}」（初判信心：{c.get('confidence') or 'low'}{why}{pf}）"]
        b += [f"  {x}" for x in c.get("contexts") or []] or ["  （逐字稿找不到出現位置）"]
        blocks.append("\n".join(b))
    return f"【詞庫】\n{'、'.join(terms) or '（無）'}\n\n【候選】\n" + "\n\n".join(blocks)


# =====================================================================================
# 完整整理（2026-10-27 spec）：引言 ＋ 大標題 ＋ 子標題。
# report.normalize_report() flattens the points into Items (group = heading, text = "標題：說明").
# =====================================================================================
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "intro": ITEM_SCHEMA,
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"heading": {"type": "string"}, "title": {"type": "string"},
                               "text": {"type": "string"}, "src": SRC_SCHEMA},
                "required": ["heading", "title", "text", "src"],
            },
        },
    },
    "required": ["intro", "points"],
}
REPORT_PROMPT = (
    "你是嚴謹的會議記錄員，這份完整整理要交給董事長閱讀，不能有任何一句沒有依據。"
    "以下是一場錄音的逐字稿（每行格式「[時:分:秒] 說話者: 內容」）。請整理成「引言」加「大標題與子標題」兩部分：\n"
    "\n"
    "■ intro（引言）：2～4 句，說明誰參與、談什麼、有哪幾個主要方向。"
    '格式 {"text":"…","group":"","src":[{"t":秒數,"quote":"逐字稿原句"}]}，引言裡的每個事實也要有 src。\n'
    "\n"
    "■ points（內容）：依錄音實際談的主題切成大標題，每一條子標題是"
    '{"heading":"大標題","title":"子標題","text":"說明","src":[{"t":秒數,"quote":"逐字稿原句"}]}。\n'
    "  - 大標題 2～6 個，每個大標題底下 2～6 個子標題；同一個大標題的 heading 用「完全相同」的文字，"
    "同一大標題的子標題連續寫在一起。不套固定範本，大標題依這場錄音的實際主題命名，依討論先後排列。\n"
    "  - title 是 4～14 字的名詞短語（例：「生意與訂單」），不寫成句子，不含冒號。\n"
    "  - text 是說明：保留數字、日期、條件、但書、誰說的；不要在 text 前面重複 title。\n"
    "  - 同一個論點只寫一條；不同議題分開寫。決議、結論要寫出是誰的意見、是否已拍板。\n"
    "  - 不要寫待辦事項與問答：它們由另外的區塊整理，這裡不重複。\n"
    "  - 不要自己加粗體或任何標記符號。\n"
    "\n"
    + GROUNDING_RULES
    + '\n只輸出 JSON {"intro":{…},"points":[…]}。' + LANG_RULE + IN_USER
)

# 分段整理後的合併：模型只回「怎麼排、怎麼分組」，文字由程式依序號取回原文，不讓模型改寫。
REPORT_MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"heading": {"type": "string"},
                               "items": {"type": "array", "items": {"type": "integer"}}},
                "required": ["heading", "items"],
            },
        },
    },
    "required": ["groups"],
}
REPORT_MERGE_PROMPT = (
    "你是會議記錄的編輯。使用者訊息是同一場錄音分段整理出來的子標題草稿，每條前面有序號，"
    "格式「[序號] 〔大標題〕子標題：說明」，序號從 1 開始（第一條是 [1]）。各段各自取的大標題可能主題相同卻名字不同，順序也只依各段內部排列。\n"
    "請只做兩件事：(1) 把主題相同的大標題合併成同一個名字（沿用草稿裡最貼切的那一個，不另創新詞，名字不超過 14 字）；"
    "(2) 決定大標題的先後順序，與每個大標題底下子標題的先後順序（依討論先後）。\n"
    "硬規則：\n"
    "- 只輸出序號，不輸出、不改寫任何子標題或說明的文字；你不可以寫新的內容。\n"
    "- 序號照草稿寫的數字（從 1 開始），每個序號必須出現且只出現一次，不可遺漏、不可重複、不可捏造不存在的序號。\n"
    "- 大標題整體 2～6 個。\n"
    '只輸出 JSON {"groups":[{"heading":"大標題","items":[序號,…]},…]}。' + LANG_RULE
)
