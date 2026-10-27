import report
from report import has_report, merge_reports, normalize_report


def _raw(points, intro="引言。"):
    return {"intro": {"text": intro, "src": [{"t": 1, "quote": "原句"}]}, "points": points}


def _pt(heading="會議討論內容", title="生意與訂單", text="元澄可提供…", src=None):
    return {"heading": heading, "title": title, "text": text, "src": src or [{"t": 410, "quote": "元澄可以提供"}]}


def test_normalize_joins_title_and_text():
    out = normalize_report(_raw([_pt()]))
    p = out["reportPoints"][0]
    assert p["text"] == "生意與訂單：元澄可提供…"
    assert p["group"] == "會議討論內容"
    assert p["src"] == [{"t": 410.0, "quote": "元澄可以提供"}]
    assert len(p["id"]) == 8
    assert out["reportIntro"][0]["text"] == "引言。"


def test_normalize_no_title_keeps_text_only():
    out = normalize_report(_raw([_pt(title="  ")]))
    assert out["reportPoints"][0]["text"] == "元澄可提供…"


def test_normalize_blank_heading_becomes_other():
    out = normalize_report(_raw([_pt(heading=" ")]))
    assert out["reportPoints"][0]["group"] == "其他"


def test_normalize_drops_empty_text():
    out = normalize_report(_raw([_pt(text="  "), _pt(text="有內容")]))
    assert [p["text"] for p in out["reportPoints"]] == ["生意與訂單：有內容"]


def test_normalize_garbage_input():
    assert normalize_report({}) == {"reportIntro": [], "reportPoints": []}
    assert normalize_report(None) == {"reportIntro": [], "reportPoints": []}


def test_merge_keeps_first_intro_and_group_order():
    a = normalize_report(_raw([_pt(heading="A", text="a1"), _pt(heading="B", text="b1")], intro="一"))
    b = normalize_report(_raw([_pt(heading="C", text="c1"), _pt(heading="A", text="a2")], intro="二"))
    m = merge_reports([a, b])
    assert m["reportIntro"][0]["text"] == "一"
    assert [p["group"] for p in m["reportPoints"]] == ["A", "A", "B", "C"]
    assert [p["text"] for p in m["reportPoints"]] == [
        "生意與訂單：a1", "生意與訂單：a2", "生意與訂單：b1", "生意與訂單：c1"]
    ids = [p["id"] for p in m["reportPoints"]]
    assert len(set(ids)) == 4


def test_merge_dedupes_and_merges_src():
    s = lambda n: [{"t": float(n), "quote": f"q{n}"}]
    a = normalize_report(_raw([_pt(text="same", src=s(1))]))
    b = normalize_report(_raw([_pt(text="same", src=s(2) + s(3) + s(4))]))
    m = merge_reports([a, b])
    assert len(m["reportPoints"]) == 1
    assert [x["quote"] for x in m["reportPoints"][0]["src"]] == ["q1", "q2", "q3"]


def test_merge_empty():
    assert merge_reports([]) == {"reportIntro": [], "reportPoints": []}


def test_has_report_false_when_only_intro():
    assert not has_report({"reportIntro": [{"id": "x", "text": "t", "src": []}], "reportPoints": []})
    assert not has_report({})
    assert not has_report(None)
    assert has_report({"reportPoints": [{"text": "x"}]})


def test_prompts_defined():
    import prompts as p
    assert p.GROUNDING_RULES in p.REPORT_PROMPT and p.LANG_RULE in p.REPORT_PROMPT and p.IN_USER in p.REPORT_PROMPT
    assert p.REPORT_SCHEMA["required"] == ["intro", "points"]
    assert p.REPORT_SCHEMA["properties"]["intro"] is p.ITEM_SCHEMA
    item = p.REPORT_SCHEMA["properties"]["points"]["items"]
    assert item["required"] == ["heading", "title", "text", "src"]
    assert p.REPORT_MERGE_SCHEMA["type"] == "object"
    assert "不" in p.REPORT_MERGE_PROMPT
