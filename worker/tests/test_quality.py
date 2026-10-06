import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asr
from pipeline import PROMPT, build_hotwords, fix_traditional, smooth_speakers


def S(spk, text, t):
    return {"speaker": spk, "text": text, "t": t}


# ---- smoothing
def test_sandwiched_short_run_merged_and_remerged():
    segs = [S("A", "今天我們來談稅", 0.0), S("B", "嗯", 10.0), S("A", "好的繼續", 11.0)]
    out = smooth_speakers(segs)
    assert len(out) == 1
    assert out[0]["speaker"] == "A" and out[0]["t"] == 0.0
    assert out[0]["text"] == "今天我們來談稅嗯好的繼續"


def test_long_duration_run_kept():
    segs = [S("A", "甲", 0.0), S("B", "對", 10.0), S("A", "乙", 14.0)]  # 4 s >= 3
    assert [s["speaker"] for s in smooth_speakers(segs)] == ["A", "B", "A"]


def test_many_chars_run_kept():
    segs = [S("A", "甲", 0.0), S("B", "一二三四五六七八九", 10.0), S("A", "乙", 11.0)]  # 9 chars > 8
    assert [s["speaker"] for s in smooth_speakers(segs)] == ["A", "B", "A"]


def test_edges_kept():
    segs = [S("B", "嗯", 0.0), S("A", "正文很長的一段話", 1.0), S("B", "好", 5.0)]
    assert [s["speaker"] for s in smooth_speakers(segs)] == ["B", "A", "B"]


def test_different_neighbours_kept_and_input_not_mutated():
    segs = [S("A", "甲", 0.0), S("B", "嗯", 1.0), S("C", "乙", 2.0)]
    assert [s["speaker"] for s in smooth_speakers(segs)] == ["A", "B", "C"]
    segs = [S("A", "甲", 0.0), S("B", "嗯", 1.0), S("A", "乙", 2.0)]
    smooth_speakers(segs)
    assert segs[1]["speaker"] == "B"


def test_multi_segment_short_run():
    segs = [S("A", "甲", 0.0), S("B", "嗯", 1.0), S("B", "對", 1.5), S("A", "乙", 2.0)]
    assert len(smooth_speakers(segs)) == 1


# ---- opencc fixes
def test_fix_traditional():
    assert fix_traditional("隻有你，隻是他，隻要好，隻能走，隻好去") == "只有你，只是他，只要好，只能走，只好去"
    assert fix_traditional("一隻有點大的貓") == "一隻有點大的貓"
    assert fix_traditional("三隻貓") == "三隻貓"


# ---- hotwords wiring
def test_build_hotwords():
    assert build_hotwords(None) is None
    assert build_hotwords([]) is None
    assert build_hotwords(["股利", " ", "ETF"]) == "股利 ETF"
    assert len(build_hotwords(["t%d" % i for i in range(300)]).split()) == 200


class FakePipe:
    def __init__(self):
        self.kw = None

    def transcribe(self, audio, **kw):
        self.kw = kw
        return iter([]), types.SimpleNamespace(duration=1.0)


def test_transcribe_passes_hotwords_and_prompt(monkeypatch):
    fp = FakePipe()
    monkeypatch.setattr(asr, "_model", object())
    monkeypatch.setattr(asr, "_pipe", fp)
    asr.transcribe_words([0.0], glossary=["股利", "CPO"])
    assert fp.kw["hotwords"] == "股利 CPO"
    assert fp.kw["initial_prompt"] == PROMPT


def test_transcribe_without_glossary_omits_hotwords(monkeypatch):
    fp = FakePipe()
    monkeypatch.setattr(asr, "_model", object())
    monkeypatch.setattr(asr, "_pipe", fp)
    asr.transcribe_words([0.0])
    assert "hotwords" not in fp.kw and fp.kw["initial_prompt"] == PROMPT
