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
    "anchor 填該節開始處逐字稿中的「一句原話」（10～20 字，必須是逐字稿裡真的出現過的字串，供跳轉定位，不可自行改寫）。\n"
    "- concepts（重要概念）：挑出需要理解的名詞或機制。term＝名詞本身；plain＝用白話說明它是什麼；why＝為什麼重要／用在哪裡。\n"
    "- tables（對照表）：把「多項目 × 多屬性」的內容還原成表格（技術優缺點比較、規格對照、各家差異等）。"
    "headers 是欄位名，rows 每一列的長度必須與 headers 相同。沒有適合的內容就回空陣列，不要硬湊。\n"
    "- figures（關鍵數據）：" + FIGURE_RULE
    + "- quiz（自我測驗）：依內容出題幫助主動回想。a 必須是逐字稿裡找得到依據的答案；沒有答案的題目不要出。\n"
    "務求忠於逐字稿，不可自行補充逐字稿沒有講到的知識。" + LANG_RULE + IN_USER
)
NOTES_SECTIONS = {
    "outline": {"label": "章節大綱", "instr": "逐一列出這段講述的「全部」章節，不要精簡、不要只挑重要的。title 為 8～20 字主題；points 為該節重點；anchor 填該節開始處逐字稿中的一句原話（10～20 字，必須真的出現過）"},
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
