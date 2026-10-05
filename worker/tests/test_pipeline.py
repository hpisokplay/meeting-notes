import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (align_words, build_segments, filter_hallucinations,
                      name_speakers, offset_parts)

TURNS = [
    {"s": 0.0, "e": 5.0, "spk": "SPEAKER_00"},
    {"s": 5.0, "e": 10.0, "spk": "SPEAKER_01"},
    {"s": 20.0, "e": 25.0, "spk": "SPEAKER_00"},
]


def test_align_words_overlap_and_nearest():
    words = [(1.0, 1.5, "你"), (4.8, 5.6, "好"), (12.0, 12.4, "嗎"), (18.0, 18.5, "對")]
    out = align_words(words, TURNS)
    # "好" overlaps 00 by 0.2 and 01 by 0.6 -> 01; 12s is nearest to turn 01 (ends 10)
    assert [w[3] for w in out] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_01", "SPEAKER_00"]
    assert out[0] == (1.0, 1.5, "你", "SPEAKER_00")
    assert align_words([], TURNS) == []
    assert align_words([(0, 1, "a")], [])[0][3] == "SPEAKER_00"  # no turns at all


def test_build_segments_merges_same_speaker_gap():
    al = [(0.0, 1.0, "你", "A"), (1.5, 2.0, "好", "A"),
          (6.0, 7.0, "再", "A"), (7.0, 7.5, "見", "B")]
    segs = build_segments(al)
    assert segs == [
        {"speaker": "A", "text": "你好", "t": 0.0},
        {"speaker": "A", "text": "再", "t": 6.0},
        {"speaker": "B", "text": "見", "t": 7.0},
    ]


def test_build_segments_splits_long():
    words = []
    t = 0.0
    for i in range(30):
        words.append((t, t + 0.3, "字字", "A"))
        t += 0.3
    words.append((t, t + 0.3, "。", "A"))
    t += 0.3
    for i in range(5):
        words.append((t, t + 0.3, "尾", "A"))
        t += 0.3
    segs = build_segments(words, max_chars=60)
    assert len(segs) == 2
    assert segs[0]["text"].endswith("。") and len(segs[0]["text"]) >= 60
    assert segs[1]["text"] == "尾" * 5
    assert segs[1]["t"] > segs[0]["t"]


PROMPT = "以下是繁體中文的會議。"


def seg(text, spk="A", t=0.0):
    return {"speaker": spk, "text": text, "t": t}


def test_filter_drops_known_hallucinations():
    segs = [seg("中文字幕由 Amara.org 社群提供"), seg("今天討論預算"), seg("謝謝收看"),
            seg("請不吝點贊訂閱"), seg("以下是繁體中文的會議。"), seg("中文的會議"),
            seg("字幕志願者 李"), seg("我們開始吧")]
    out = filter_hallucinations(segs, PROMPT)
    assert [s["text"] for s in out] == ["今天討論預算", "我們開始吧"]


def test_filter_collapses_repeats():
    segs = [seg("好"), seg("好"), seg("好"), seg("好"), seg("嗯"), seg("好"), seg("好")]
    out = filter_hallucinations(segs, PROMPT)
    assert [s["text"] for s in out] == ["好", "嗯", "好", "好"]


def test_name_speakers_order():
    segs = [seg("a", "SPEAKER_02"), seg("b", "SPEAKER_00"), seg("c", "SPEAKER_02")]
    out = name_speakers(segs)
    assert [s["speaker"] for s in out] == ["說話者 1", "說話者 2", "說話者 1"]


def test_offset_parts():
    parts = [[seg("a", t=1.0)], [seg("b", t=2.5)], [seg("c", t=0.0)]]
    out = offset_parts(parts, [10.0, 20.0, 5.0])
    assert [s["t"] for s in out] == [1.0, 12.5, 30.0]
    assert [s["text"] for s in out] == ["a", "b", "c"]
