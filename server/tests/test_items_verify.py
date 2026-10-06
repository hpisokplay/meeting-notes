"""Task B: items.py (ids / legacy normalisation) and verify.py (待確認清單 checks)."""
import json

from server import items as I
from server import prompts as P
from server import verify as V

TRANSCRIPT = [
    {"speaker": "會計師", "t": 0.0, "text": "各位好，今天許會跟大家談稅務。民國108年資金回臺專法通過"},
    {"speaker": "會計師", "t": 30.0, "text": "我繳一億的稅，如果對砍，只要繳五千塊，是不是差很多？"},
    {"speaker": "會計師", "t": 200.0, "text": "鼓勵所得分離課稅28%，大家要小心，不要亂做"},
    {"speaker": "會計師", "t": 400.0, "text": "新加坡賣股票五千萬要回台灣，國稅局會盯上"},
    {"speaker": "會計師", "t": 520.0, "text": "用百分之二十推定所得，再乘以百分之二十的最低稅負"},
    {"speaker": "會計師", "t": 700.0, "text": "金融資訊交換CIS目前只跟英國日本交換，宋秀玲宋署長也說過"},
    {"speaker": "聽眾", "t": 900.0, "text": "請問大概三四千億回來嗎"},
]


def item(text, *src):
    return {"id": I.hash8(text), "text": text, "src": [{"t": t, "quote": q} for t, q in src]}


def meeting(summary=None, notes=None, **kw):
    m = {"id": "m1", "transcript": TRANSCRIPT, "durationSec": 1000, "summary": summary or {}}
    if notes is not None:
        m["notes"] = notes
    m.update(kw)
    return m


def kinds(review):
    return sorted(r["kind"] for r in review)


# ---------------------------------------------------------------- items
def test_normalize_item_from_legacy_string():
    it = I.normalize_item("重點一")
    assert it == {"id": I.hash8("重點一"), "text": "重點一", "src": []}
    assert len(it["id"]) == 8 and int(it["id"], 16) >= 0


def test_normalize_item_keeps_valid_id_group_and_coerces_src():
    it = I.normalize_item({"id": "deadbeef", "text": "x", "group": " 稅 ",
                           "src": [{"t": "00:22:58", "quote": "只要繳五千塊"}, {"t": "bad"}, "junk"]})
    assert it == {"id": "deadbeef", "text": "x", "group": "稅", "src": [{"t": 1378.0, "quote": "只要繳五千塊"}]}
    assert I.normalize_item({"id": "NOTHEX!!", "text": "y"})["id"] == I.hash8("y")


def test_normalize_item_legacy_qa_dict():
    assert I.normalize_item({"q": "問？", "a": "答。"})["text"] == "問：問？\n答：答。"


def test_ensure_ids_unique_and_notes_objects_keep_fields():
    out = I.ensure_ids(["同一句", "同一句", {"text": "另一句", "src": []}])
    assert len({x["id"] for x in out}) == 3
    notes = I.ensure_ids([{"q": "Q", "a": "A"}, {"label": "稅率", "value": "28%"}], notes=True)
    assert notes[0]["q"] == "Q" and notes[0]["src"] == [] and len(notes[0]["id"]) == 8
    assert notes[1]["value"] == "28%" and "text" not in notes[1]


def test_iter_items_ids_match_ensure_ids():
    m = meeting({"mainPoints": ["a", {"id": "0000abcd", "text": "b", "src": []}]},
                {"figures": [{"label": "L", "value": "1億"}]})
    got = [(s, iid, txt) for s, _, iid, txt, _ in I.iter_items(m)]
    assert got == [("summary.mainPoints", I.hash8("a"), "a"), ("summary.mainPoints", "0000abcd", "b"),
                   ("notes.figures", I.hash8("L：1億"), "L：1億")]


def test_parse_time_forms():
    assert I.parse_time("01:02") == 62 and I.parse_time("01:00:01") == 3601 and I.parse_time(5) == 5.0
    assert I.parse_time("x") is None and I.parse_time(True) is None and I.parse_time(-1) is None


# ---------------------------------------------------------------- numbers
def test_parse_chinese_numbers():
    assert V.parse_number("五千萬") == [50000000]
    assert V.parse_number("一億") == [100000000]
    assert V.parse_number("5000萬") == [50000000]
    assert V.parse_number("3.5億") == [350000000]
    assert V.parse_number("一億兩千萬") == [120000000]
    assert V.parse_number("十二") == [12]
    assert V.parse_number("一零八") == [108]
    assert V.parse_number("一百零五") == [105]
    assert set(V.parse_number("三四千億")) == {3e11, 4e11}
    assert 2500 in V.parse_number("兩千五")


def test_extract_numbers_percent_year_and_strict_filters():
    vals = lambda s, strict=True: [n["values"] for n in V.extract_numbers(s, strict)]  # noqa: E731
    assert vals("稅率28%") == [{28}]
    assert vals("百分之二十八") == [{28}]
    assert 2019 in vals("民國108年")[0] and 108 in vals("民國108年")[0]
    assert 108 in vals("2019年")[0]
    assert vals("九成五為台灣人") == [{9, 95}] or 95 in vals("九成五為台灣人")[0]
    assert vals("B200 晶片與 400G 模組、5G") == []
    assert vals("千萬不要這樣做，萬一被查") == []
    assert vals("一般來說兩個人，第三點") == []
    assert vals("二三代責任") == []
    assert vals("1,000萬") == [{1e7}]


# ---------------------------------------------------------------- verify: quote
def test_quote_found_exact_fuzzy_and_missing():
    ok = item("股利分離課稅：28%", (200, "鼓勵所得分離課稅28%"))
    fuzzy = item("股利分離課稅：28%", (200, "鼓勵所得 分離課稅 28 %，大家要小心"))  # spacing / punctuation
    near = item("股利分離課稅：28%", (200, "鼓勵所得分離課稅28%大家都要小心"))  # one char added
    bad = item("講者說要全部移民", (200, "大家一定要全部移民新加坡"))
    r = V.verify_meeting(meeting({"mainPoints": [ok, fuzzy, near, bad]}))
    flagged = {x["itemId"] for x in r if x["kind"] == "quote"}
    assert flagged == {bad["id"]}
    q = next(x for x in r if x["kind"] == "quote")
    assert q["section"] == "summary.mainPoints" and q["state"] == "pending" and "找不到" in q["detail"]
    assert q["text"] == "講者說要全部移民" and q["t"] == 200


def test_new_item_without_src_flagged_but_legacy_string_not():
    r = V.verify_meeting(meeting({"mainPoints": ["舊版字串重點", {"text": "新版沒有出處", "src": []}]}))
    assert [(x["kind"], x["text"]) for x in r] == [("quote", "新版沒有出處")]


# ---------------------------------------------------------------- verify: number
def test_number_equivalence_within_window():
    good = [
        item("海外所得：5000萬", (400, "新加坡賣股票五千萬")),
        item("稅額：1億元", (30, "我繳一億的稅")),
        item("股利稅率：28%", (200, "鼓勵所得分離課稅28%")),
        item("資金回台專法：民國108年通過", (0, "民國108年資金回臺專法通過")),
        item("資金回台專法：2019年通過", (0, "民國108年資金回臺專法通過")),
        item("最低稅負：20%", (520, "百分之二十的最低稅負")),
        item("回流金額：約3000億至4000億元", (900, "請問大概三四千億回來嗎")),
    ]
    r = V.verify_meeting(meeting({"mainPoints": good}))
    assert [x for x in r if x["kind"] == "number"] == []


def test_number_outside_window_or_absent_flagged():
    silently_fixed = item("降稅：只要繳5000萬", (30, "只要繳五千塊"))  # 五千萬 only said at 400 s
    invented = item("推計所得：12%", (520, "用百分之二十推定所得"))
    r = V.verify_meeting(meeting({"mainPoints": [silently_fixed, invented]}))
    nums = {x["itemId"]: x["detail"] for x in r if x["kind"] == "number"}
    assert set(nums) == {silently_fixed["id"], invented["id"]}
    assert "5000萬" in nums[silently_fixed["id"]] and "60 秒" in nums[silently_fixed["id"]]
    assert "12%" in nums[invented["id"]]


def test_number_without_src_checked_against_whole_transcript():
    r = V.verify_meeting(meeting({"mainPoints": ["海外所得5000萬", "稅率33%"]}))
    assert [(x["kind"], x["text"]) for x in r] == [("number", "稅率33%")]


# ---------------------------------------------------------------- verify: name
def test_names_title_glossary_latin_quoted_corrections():
    fabricated = item("講師：許崑源會計師為兼任教授", (0, "今天許會跟大家談稅務"))
    fine = item("講師：許會計師提到宋秀玲署長", (700, "宋秀玲宋署長也說過"))
    latin = item("資訊交換：CRS 只跟英國日本交換", (700, "金融資訊交換CIS目前只跟英國日本交換"))
    gloss = item("股利所得：分離課稅28%", (200, "鼓勵所得分離課稅28%"))
    quoted = item("講者稱為「海外信託大法」", (200, "鼓勵所得分離課稅28%"))
    r = V.verify_meeting(meeting({"mainPoints": [fabricated, fine, latin, gloss, quoted]}),
                         glossary="股利所得\nCRS\n")
    names = {x["itemId"]: x["detail"] for x in r if x["kind"] == "name"}
    assert "許崑源" in names[fabricated["id"]]
    assert fine["id"] not in names
    assert "CRS" in names[latin["id"]]
    assert "股利所得" in names[gloss["id"]]
    assert "海外信託大法" in names[quoted["id"]]


def test_name_from_corrections_right_passes_once_applied():
    it = item("股利所得：分離課稅28%", (200, "所得分離課稅28%"))
    corr = {"items": [{"wrong": "鼓勵所得", "right": "股利所得", "confidence": "high", "count": 1,
                       "ts": [200], "state": "applied"}]}
    r = V.verify_meeting(meeting({"mainPoints": [it]}, corrections=corr))
    assert [x["kind"] for x in r] == ["name"]  # transcript still says 鼓勵所得 here
    applied = [dict(s, text=s["text"].replace("鼓勵所得", "股利所得")) for s in TRANSCRIPT]
    assert V.verify_meeting(meeting({"mainPoints": [it]}, corrections=corr, transcript=applied)) == []


# ---------------------------------------------------------------- verify: calc
def test_calc_checked():
    wrong = item("推定稅負：20%×20%=8%", (520, "用百分之二十推定所得"))
    right = item("推定稅負：20%×20%=4%", (520, "用百分之二十推定所得"))
    r = V.verify_meeting(meeting({"mainPoints": [wrong, right]}))
    calc = [x for x in r if x["kind"] == "calc"]
    assert [x["itemId"] for x in calc] == [wrong["id"]]
    assert calc[0]["suggestion"] == "推定稅負：20%×20%=4%"
    assert V.calc_problems("60億×40%=24億") == []
    assert V.calc_problems("12%×20%=4.8%") == [("12%×20%=4.8%", "12%×20%=2.4%")]
    assert V.calc_problems("3*4=13") == [("3*4=13", "3*4=12")]


# ---------------------------------------------------------------- terms, ids, merge
def test_pending_corrections_become_term_items():
    corr = {"items": [
        {"wrong": "證與", "right": "贈與", "confidence": "medium", "count": 3, "ts": [11.5], "state": "pending"},
        {"wrong": "鼓勵所得", "right": "股利所得", "confidence": "high", "count": 1, "ts": [200], "state": "applied"},
    ]}
    r = V.verify_meeting(meeting(corrections=corr))
    assert len(r) == 1
    t = r[0]
    assert (t["kind"], t["section"], t["text"], t["suggestion"], t["t"]) == ("term", "transcript", "證與", "贈與", 11.5)
    assert "中" in t["detail"] and "3 次" in t["detail"]


def test_ids_stable_and_states_merged():
    bad = item("講師：許崑源會計師", (0, "今天許會跟大家談稅務"))
    m = meeting({"mainPoints": [bad]})
    first = V.verify_meeting(m)
    assert first == V.verify_meeting(m)  # deterministic ids
    assert first[0]["id"] == V.review_item("name", "summary.mainPoints", bad["id"], bad["text"], "x")["id"]
    m["review"] = {"items": [dict(first[0], state="confirmed")]}
    again = V.verify_meeting(m)
    assert again[0]["state"] == "confirmed" and V.pending_count({"items": again}) == 0


def test_extra_slip_items_and_carry_over():
    slip = V.review_item("slip", "transcript", None, "只要繳五千塊", "依上下文疑為五千萬", t=30)
    m = meeting()
    r = V.verify_meeting(m, extra=[slip])
    assert [x["kind"] for x in r] == ["slip"]
    m["review"] = {"items": [dict(slip, state="confirmed")]}
    kept = V.verify_meeting(m)  # re-verify without re-running SLIPS keeps the handled slip
    assert [(x["kind"], x["state"]) for x in kept] == [("slip", "confirmed")]
    replaced = V.verify_meeting(m, extra=[])
    assert [x["kind"] for x in replaced] == ["slip"]


def test_notes_entries_verified():
    notes = {"figures": [{"label": "推計所得率", "value": "12%", "src": [{"t": 520, "quote": "用百分之二十推定所得"}]}],
             "quiz": [{"q": "誰是講師？", "a": "許崑源會計師"}],  # legacy (no src key): no quote flag
             "outline": [{"title": "開場", "points": ["許會介紹"], "src": [{"t": 0, "quote": "今天許會跟大家談稅務"}]}],
             "generatedAt": 1}
    r = V.verify_meeting(meeting(notes=notes))
    assert sorted((x["section"], x["kind"]) for x in r) == [("notes.figures", "number"), ("notes.quiz", "name")]


def test_legacy_meeting_fixture_and_odd_shapes(meeting):
    meeting["summary"]["qa"].append(None)
    r = V.verify_meeting(meeting)
    assert isinstance(r, list)
    assert V.verify_meeting({"id": "x"}) == []


# ---------------------------------------------------------------- prompts
def test_new_prompts_carry_rules_and_schemas_require_src():
    rules = ["只能寫逐字稿裡有的內容", "不可補人名全名、職稱、機構、數字", "講者原話為…，依上下文疑為…",
             "數字照原文單位，不可自行換算", "每條附 1–3 個 src（t 為秒、quote 為逐字稿原句 6–40 字）",
             "講者的但書、警告（不要、小心、注意、不建議）必須保留在同一條"]
    for p in (P.SUMMARY_MEETING, P.SUMMARY_LECTURE, P.NOTES, P.enhance_src_prompt("risks")):
        for rule in rules:
            assert rule in p
        assert "繁體中文" in p
    assert "最多 25 條" in P.SUMMARY_LECTURE and "最多 15 條" in P.SUMMARY_MEETING
    assert "actionItems" not in P.SUMMARY_LECTURE
    for key in ("conclusions", "advice", "risks", "cases", "mainPoints", "qa"):
        it = P.SUMMARY_LECTURE_SCHEMA["properties"][key]["items"]
        assert it["required"] == ["text", "src"]
        assert it["properties"]["src"]["items"]["required"] == ["t", "quote"]
    for sec in ("outline", "concepts", "tables", "figures", "quiz"):
        assert "src" in P.NOTES_SRC_SCHEMA["properties"][sec]["items"]["required"]
    assert P.MODE_SCHEMA["properties"]["mode"]["enum"] == ["lecture", "meeting"]
    assert set(P.CORRECTION_SCHEMA["properties"]["items"]["items"]["required"]) == {
        "wrong", "right", "confidence", "reason", "ts"}
    assert set(P.SLIPS_SCHEMA["properties"]["items"]["items"]["required"]) == {"t", "quote", "issue", "suggestion"}
    msg = P.correction_user_message("[00:00:01] A: 鼓勵所得", "股利所得\nCRS\n")
    assert "股利所得、CRS" in msg and "鼓勵所得" in msg
    json.dumps([P.SUMMARY_MEETING_SCHEMA, P.NOTES_SRC_SCHEMA, P.ITEMS_SRC_SCHEMA, P.POLISH_SRC_SCHEMA])
    # existing prompts used by enhance / ask / terms / translate are untouched
    assert P.SUMMARY_PROMPT and P.TERMS_PROMPT and P.ASK_PROMPT and P.NOTES_PROMPT and P.translate_prompt("en")


# ================================================================ fix round 1
def test_fix1_extra_empty_clears_old_pending_slips_but_keeps_handled():
    pend = V.review_item("slip", "transcript", None, "只要繳五千塊", "疑為五千萬", t=30)
    done = V.review_item("slip", "transcript", None, "133天", "疑為183天", t=60)
    other = V.review_item("custom", "transcript", None, "x", "y")
    m = meeting(review={"items": [pend, dict(done, state="confirmed"), other]})
    assert {x["id"] for x in V.verify_meeting(m)} == {pend["id"], done["id"], other["id"]}  # extra=None keeps
    r = V.verify_meeting(m, extra=[])
    assert [(x["id"], x["state"]) for x in r] == [(done["id"], "confirmed"), (other["id"], "pending")]
    r2 = V.verify_meeting(m, extra=[], recomputed_kinds={"slip", "custom"})
    assert [x["id"] for x in r2] == [done["id"]]
    again = V.verify_meeting(m, extra=[pend])  # same slip re-emitted keeps its id, still pending
    assert any(x["id"] == pend["id"] and x["state"] == "pending" for x in again)


def test_fix2_calc_forms():
    assert V.calc_problems("1,000萬×40%=400萬") == []
    assert V.calc_problems("3千萬×20%=600萬") == []
    assert V.calc_problems("1+2×3=7") == []  # not a single binary multiplication
    assert V.calc_problems("2×3×4=25") == []
    assert V.calc_problems("12%×20%=約2.5%") == [] and V.calc_problems("12%×20%≈2.5%") == []
    assert V.calc_problems("1000×3%=30.2") == []  # within ±1%
    assert V.calc_problems("售價1,000萬×40%=500萬") == [("1,000萬×40%=500萬", "1,000萬×40%=400萬")]
    assert V.calc_problems("v1.5,2×3=7") == []  # glued to a preceding number
    two = item("算式：2×3=7，又2×3=7", (520, "用百分之二十推定所得"))
    c = [x for x in V.verify_meeting(meeting({"mainPoints": [two]})) if x["kind"] == "calc"]
    assert c and "suggestion" not in c[0]  # ambiguous replacement -> no suggestion


SPAN_TX = [
    {"speaker": "A", "t": 0.0, "text": "第一節開始，今天談海外所得"},
    {"speaker": "A", "t": 100.0, "text": "海外所得扣除750萬"},
    {"speaker": "A", "t": 200.0, "text": "再乘以20%算基本稅額"},
    {"speaker": "A", "t": 300.0, "text": "持股30%以上要申報"},
    {"speaker": "A", "t": 400.0, "text": "獲利700萬以上才算"},
    {"speaker": "A", "t": 500.0, "text": "推定所得率百分之十二"},
    {"speaker": "A", "t": 600.0, "text": "持股滿3年折半，扣繳21%"},
    {"speaker": "A", "t": 700.0, "text": "利息扣繳20%"},
    {"speaker": "A", "t": 800.0, "text": "第二節開始，談信託，稅率99%"},
]


def test_fix3_outline_section_span_window():
    sec1 = {"title": "海外所得與CFC", "points": ["扣除750萬", "乘以20%", "持股30%", "獲利700萬", "推定12%",
                                             "扣繳21%", "利息20%", "第一節"],
            "src": [{"t": 0, "quote": "第一節開始，今天談海外所得"}]}
    sec2 = {"title": "信託", "points": ["稅率99%"], "src": [{"t": 800, "quote": "第二節開始，談信託"}]}
    table = {"title": "比較", "headers": ["項目", "稅率"], "rows": [["扣繳", "21%"], ["利息", "20%"], ["X", "99%"]],
             "src": [{"t": 100, "quote": "海外所得扣除750萬"}]}
    m = {"transcript": SPAN_TX, "summary": {}, "notes": {"outline": [sec1, sec2], "tables": [table]}}
    r = V.verify_meeting(m)
    nums = [(x["section"], x["detail"]) for x in r if x["kind"] == "number"]
    assert len(nums) == 1 and nums[0][0] == "notes.tables" and "99%" in nums[0][1] and "本節時間範圍" in nums[0][1]


def test_fix4_chinese_decimals_and_ge():
    assert V.parse_number("十二點五") == [12.5]
    assert V.parse_number("三點五億") == [3.5e8]
    assert V.parse_number("一個億") == [1e8] and V.parse_number("兩個億") == [2e8]
    tx = [{"speaker": "A", "t": 0, "text": "稅率十二點五趴，賺了三點五億，罰了兩個億"}]
    it = item("稅率12.5%，獲利3.5億元，罰款2億", (0, "稅率十二點五趴，賺了三點五億"))
    assert [x for x in V.verify_meeting({"transcript": tx, "summary": {"mainPoints": [it]}})
            if x["kind"] == "number"] == []
    assert V.extract_numbers("重點是一點點", strict=True) == []


def test_fix5_edited_state_carried_by_item_key():
    bad = item("稅率：33%", (200, "鼓勵所得分離課稅28%"))
    m = meeting({"mainPoints": [bad]})
    first = V.verify_meeting(m)
    assert [x["kind"] for x in first] == ["number"]
    m["review"] = {"items": [dict(first[0], state="edited")]}
    m["summary"]["mainPoints"] = [dict(bad, text="稅率：35%")]  # user edit kept the item id
    again = V.verify_meeting(m)
    assert [(x["kind"], x["state"]) for x in again] == [("number", "edited")]


def test_fix6_near_miss_person_name_and_negatives():
    tx = [{"speaker": "A", "t": 0, "text": "台塑王家第二代王文曹他太太是外國人，大家高度重視，許多建商都知道"}]
    it = item("配偶案例：台塑第二代王文洋，配偶為外國人", (0, "台塑王家第二代王文曹"))
    r = V.verify_meeting({"transcript": tx, "summary": {"mainPoints": [it]}})
    names = [x["detail"] for x in r if x["kind"] == "name"]
    assert names == ["逐字稿為『王文曹』，條目寫『王文洋』。"]
    ok1 = item("講者態度：主管機關高度重組，，", (0, "大家高度重視"))
    ok2 = item("市場：許多建案。", (0, "許多建商都知道"))
    ok3 = item("市場：許多建商都知道", (0, "許多建商都知道"))
    r = V.verify_meeting({"transcript": tx, "summary": {"mainPoints": [ok1, ok2, ok3]}})
    assert [x for x in r if x["kind"] == "name"] == []
    assert V.near_miss_names("高度重視，許多建商", V.TranscriptIndex(tx)) == []


def test_fix6_short_quotes_need_exact_match():
    short_bad = item("稅率：28%", (200, "鼓勵所得分離課稅29%"))  # 12 chars, one differs
    long_ok = item("稅率：28%", (200, "鼓勵所得分離課稅28%，大家要小心不要亂作"))  # long, one differs
    r = V.verify_meeting(meeting({"mainPoints": [short_bad, long_ok]}))
    assert {x["itemId"] for x in r if x["kind"] == "quote"} == {short_bad["id"]}


def test_fix_minor_suspected_numbers_term_ids_variants_relocate():
    it = item("講者原話為「只要繳五千塊」，依上下文疑為五千萬", (30, "只要繳五千塊，是不是差很多"))
    assert V.verify_meeting(meeting({"mainPoints": [it]})) == []
    corr = {"items": [{"wrong": "證與", "right": "贈與", "confidence": "low", "state": "pending"},
                      {"wrong": "證與", "right": "證據", "confidence": "low", "state": "pending"}]}
    ids = [x["id"] for x in V.verify_meeting(meeting(corrections=corr))]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert V.norm("裏面着綫") == V.norm("裡面著線")
    m = meeting({"mainPoints": [item("稅率：28%", (5, "鼓勵所得分離課稅28%")), "舊字串"]},
                {"quiz": [{"q": "Q", "a": "A", "src": [{"t": 0, "quote": "新加坡賣股票五千萬要回台灣"}]}]})
    assert V.relocate_src(m) == 2
    assert m["summary"]["mainPoints"][0]["src"][0]["t"] == 200.0
    assert m["notes"]["quiz"][0]["src"][0]["t"] == 400.0
    assert V.relocate_src(m) == 0


def test_fix7_8_prompt_wording():
    for sec in P.NOTES_SRC_SECTIONS:
        p = P.notes_src_enhance_prompt(sec)
        for pressure in ("不要精簡", "不要只挑", "務求完整", "每一個重要知識點"):
            assert pressure not in p
    assert "務求完整" not in P.enhance_src_prompt("mainPoints")
    assert "講者自己的解釋" in P.NOTES_SRC_PROMPT and "用白話說明它是什麼" not in P.NOTES_SRC_PROMPT
    assert "張冠李戴" in P.GROUNDING_RULES and "遺漏條件" in P.GROUNDING_RULES and "2 年內" in P.GROUNDING_RULES
