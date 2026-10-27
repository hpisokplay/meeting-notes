from docx import Document

from server.render import _report_outline, meeting_to_docx, meeting_to_markdown


def _it(text, group=None):
    d = {"id": "aaaaaaaa", "text": text, "src": []}
    if group is not None:
        d["group"] = group
    return d


def _m(points, intro="引言一段。", **extra):
    m = {"title": "T", "summary": {"reportIntro": [_it(intro)] if intro else [], "reportPoints": points,
                                   "actionItems": [], "qa": []}, "transcript": []}
    m["summary"].update(extra)
    return m


def test_outline_no_colon_gives_empty_title():
    intro, secs = _report_outline(_m([_it("沒有冒號的句子", "甲")]))
    assert intro == "引言一段。"
    assert secs == [("甲", [("", "沒有冒號的句子")])]


def test_outline_colon_rules():
    _, secs = _report_outline(_m([_it("：開頭冒號", "甲"), _it("a:b 半形", "甲"), _it("標：說：明", "甲")]))
    assert secs[0][1] == [("", "開頭冒號"), ("", "a:b 半形"), ("標", "說：明")]


def test_outline_group_order_and_other():
    _, secs = _report_outline(_m([_it("x：1", "乙"), _it("y：2"), _it("z：3", "甲"), _it("w：4", "乙")]))
    assert [h for h, _ in secs] == ["乙", "其他", "甲"]
    assert secs[0][1] == [("x", "1"), ("w", "4")]


def test_outline_no_group_single_empty_heading():
    _, secs = _report_outline(_m([_it("x：1"), _it("y：2")]))
    assert [h for h, _ in secs] == [""]


def test_md_report_order():
    m = _m([_it("甲標：甲說明", "大一"), _it("乙標：乙說明", "大二")],
           actionItems=[_it("做事一"), _it("做事二")], qa=[_it("問：為何？\n答：因為。")])
    md = meeting_to_markdown(m)
    order = ["引言一段。", "## 1. 大一", "## 2. 大二", "## Action Items", "i. 做事一", "ii. 做事二", "## Q&A", "## 逐字稿"]
    pos = [md.index(s) for s in order]
    assert pos == sorted(pos)
    assert "會議重點" not in md and "## 待辦" not in md


def test_md_sub_title_bold():
    md = meeting_to_markdown(_m([_it("小標：說明", "大"), _it("只有文字", "大")]))
    assert "- **小標：**說明" in md
    assert "- 只有文字" in md


def test_md_empty_blocks_omitted():
    md = meeting_to_markdown(_m([_it("a：b", "大")]))
    assert "## Action Items" not in md and "## Q&A" not in md


def test_md_empty_heading_not_numbered():
    md = meeting_to_markdown(_m([_it("a：b"), _it("c：d")]))
    assert "- **a：**b" in md and "## 1." not in md
    md2 = meeting_to_markdown(_m([_it("a：b", "  "), _it("c：d", "大")]))
    assert "## 1. 大" in md2


def test_md_legacy_unchanged():
    m = {"title": "T", "createdAt": 0, "summary": {"actionItems": ["a"], "mainPoints": ["b"], "qa": []},
         "transcript": [{"t": 1, "speaker": "S", "text": "hi"}]}
    assert meeting_to_markdown(m) == meeting_to_markdown({**m, "summary": {**m["summary"], "reportPoints": [], "reportIntro": []}})
    assert "## 待辦\n- a" in meeting_to_markdown(m)


def test_docx_report(tmp_path):
    m = _m([_it("小標：說明", "大一"), _it("無標題", "大一")], actionItems=[_it("做事")], qa=[_it("問：Q\n答：A")])
    p = tmp_path / "r.docx"
    meeting_to_docx(m, p)
    d = Document(str(p))
    heads = [x.text for x in d.paragraphs if x.style.name == "Heading 1"]
    assert heads[0] == "1. 大一" and "Action Items" in heads and "Q&A" in heads and "逐字稿" in heads
    bullets = [x for x in d.paragraphs if x.style.name == "List Bullet"]
    assert len(bullets) == 2
    bold = [r for r in bullets[0].runs if r.bold]
    assert [r.text for r in bold] == ["小標："]
    assert bullets[0].text == "小標：說明"
    assert not any(r.bold for r in bullets[1].runs)
    texts = [x.text for x in d.paragraphs]
    assert "引言一段。" in texts and "i. 做事" in texts
    assert texts.index("引言一段。") < texts.index("1. 大一")
