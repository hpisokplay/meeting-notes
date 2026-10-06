"""faster-whisper large-v3 on the GPU, word timestamps. Imports are lazy so unit tests need no GPU."""
import gc
import os
import subprocess
import sys

from pipeline import PROMPT, build_hotwords

_model = None
_pipe = None


def setup_cuda_dlls():
    """pip `nvidia-*` wheels keep cuBLAS/cuDNN DLLs under site-packages/nvidia/*/bin on Windows."""
    if os.name != "nt":
        return
    import site
    from pathlib import Path
    roots = site.getsitepackages() + [site.getusersitepackages()]
    for p in roots:
        root = Path(p) / "nvidia"
        if root.is_dir():
            for b in root.glob("*/bin"):
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def load_audio(path):
    """Decode any audio/video to 16 kHz mono float32 via ffmpeg (no PyAV)."""
    import numpy as np
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
             "-f", "s16le", "-"],
            capture_output=True, creationflags=flags)
    except FileNotFoundError:
        raise RuntimeError("找不到 ffmpeg，請先安裝")
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("ffmpeg 無法解碼音檔：" + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe_words(audio, on_progress=None, glossary=None):
    """Returns [(start, end, word)]. on_progress(fraction 0..1) called as segments arrive."""
    global _model, _pipe
    setup_cuda_dlls()
    if _model is None:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        _model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        _pipe = BatchedInferencePipeline(model=_model)
    kw = {}
    hot = build_hotwords(glossary)
    if hot:  # faster-whisper 1.2.1 BatchedInferencePipeline.transcribe supports hotwords
        kw["hotwords"] = hot
    segs, info = _pipe.transcribe(
        audio, batch_size=8, language="zh", vad_filter=True, beam_size=5,
        condition_on_previous_text=False, word_timestamps=True, initial_prompt=PROMPT, **kw)
    dur = max(info.duration, 1e-6)
    words = []
    for s in segs:
        for w in (s.words or []):
            words.append((float(w.start), float(w.end), w.word))
        if on_progress:
            on_progress(min(1.0, s.end / dur))
    return words


def release():
    """Free GPU memory before pyannote runs."""
    global _model, _pipe
    _pipe = None
    _model = None
    gc.collect()
    if "torch" in sys.modules:
        import torch
        torch.cuda.empty_cache()
