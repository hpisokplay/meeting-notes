import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asr
from pipeline import PROMPT, build_hotwords, fix_traditional, smooth_speakers


def S(spk, text, t):
    return {"speaker": spk, "text": text, "t": t}


# ---- smoothing
def test_sandwiched_short_run_relabelled_not_merged():
    segs = [S("A", "今天我們來談稅", 0.0), S("B", "嗯", 10.0), S("A", "好的繼續", 11.0)]
    out = smooth_speakers(segs)
    assert [s["speaker"] for s in out] == ["A", "A", "A"]
    assert [s["text"] for s in out] == ["今天我們來談稅", "嗯", "好的繼續"]
    assert [s["t"] for s in out] == [0.0, 10.0, 11.0]


def test_long_single_speaker_lecture_keeps_segment_count():
    """Regression: 89-min lecture collapsed to 7 segments because smoothing re-merged
    every adjacent same-speaker line."""
    import random
    rnd = random.Random(7)
    phrase = "所以股利所得可以選擇合併計稅或者分離課稅，"
    segs, t = [], 0.0
    for i in range(300):
        n = rnd.randint(20, 60)
        segs.append(S("SPEAKER_00", (phrase * 4)[:n] + "。", t))
        t += rnd.uniform(3.0, 15.0)
        if i % 37 == 5:  # diarization flicker: 1-2 char other-speaker line
            segs.append(S("SPEAKER_01", "嗯" if i % 2 else "對對", t))
            t += 0.8
    out = smooth_speakers(segs)
    assert abs(len(out) - len(segs)) <= 0.05 * len(segs)
    assert max(len(s["text"]) for s in out) <= 120
    assert {s["speaker"] for s in out} == {"SPEAKER_00"}


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
    assert [s["speaker"] for s in smooth_speakers(segs)] == ["A"] * 4


# ---- opencc fixes
def test_fix_traditional():
    assert fix_traditional("隻有你，隻是他，隻要好，隻能走，隻好去") == "只有你，只是他，只要好，只能走，只好去"
    assert fix_traditional("一隻有點大的貓") == "一隻有點大的貓"
    assert fix_traditional("三隻貓") == "三隻貓"
    assert fix_traditional("兩隻好狗") == "兩隻好狗"
    assert fix_traditional("三隻有") == "三隻有"
    assert fix_traditional("隻有") == "只有"


# ---- hotwords wiring
def test_build_hotwords():
    assert build_hotwords(None) is None
    assert build_hotwords([]) is None
    assert build_hotwords(["股利", " ", "ETF"]) == "股利 ETF"


def test_hotwords_capped_by_size_and_warns(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="worker"):
        out = build_hotwords(["t%d" % i for i in range(300)])
    assert len(out.split()) == 60
    assert "dropped 240 of 300" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="worker"):
        out = build_hotwords(["一二三四五六七八九十"] * 10)  # 10 chars each -> 6 fit
    assert len(out.split()) == 6 and "dropped 4 of 10" in caplog.text


def test_hotwords_small_glossary_no_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="worker"):
        assert build_hotwords(["Pilot line", "股利"]) == "Pilot line 股利"
    assert caplog.text == ""


def test_flicker_ababa_collapses():
    segs = [S("A", "正文很長", 0.0), S("B", "嗯", 5.0), S("A", "對", 6.0), S("B", "好", 7.0), S("A", "繼續", 8.0)]
    out = smooth_speakers(segs)
    assert len(out) == 5 and {s["speaker"] for s in out} == {"A"}


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
