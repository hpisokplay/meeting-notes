"""faster-whisper large-v3 on the GPU, word timestamps. Imports are lazy so unit tests need no GPU."""
import gc
import os
import subprocess
import sys

from pipeline import PROMPT

_model = None
_pipe = None
_compute_type = None


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


def free_vram_mb():
    """Free memory on GPU 0 in MiB (whole card, incl. ComfyUI etc.), or None when unknown.
    nvidia-smi on purpose, not torch.cuda.mem_get_info: the long-lived worker process must never
    create a CUDA context (a display-driver reset leaves it broken); GPU work runs in children."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, timeout=20, creationflags=flags)
        if p.returncode == 0:
            return int(p.stdout.decode("ascii", "replace").split()[0])
    except Exception:
        pass
    return None


def transcribe_words(audio, on_progress=None, compute_type="float16", batch_size=8):
    """Returns [(start, end, word)]. on_progress(fraction 0..1) called as segments arrive.
    compute_type/batch_size are lowered by the worker when the GPU is short of memory.
    Neutral prompt only: glossary terms (as `hotwords` or inside initial_prompt) were measured
    to drop words (5-8%) and punctuation (~50%) on a 10-min lecture clip, and slow ASR 1.6-1.8x."""
    global _model, _pipe, _compute_type
    setup_cuda_dlls()
    if _model is not None and _compute_type not in (None, compute_type):
        release()
    if _model is None:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        _model = WhisperModel("large-v3", device="cuda", compute_type=compute_type)
        _pipe = BatchedInferencePipeline(model=_model)
        _compute_type = compute_type
    segs, info = _pipe.transcribe(
        audio, batch_size=batch_size, language="zh", vad_filter=True, beam_size=5,
        condition_on_previous_text=False, word_timestamps=True, initial_prompt=PROMPT)
    dur = max(info.duration, 1e-6)
    words = []
    for s in segs:
        for w in (s.words or []):
            words.append((float(w.start), float(w.end), w.word))
        if on_progress:
            on_progress(min(1.0, s.end / dur))
    return words


def release():
    """Free GPU memory (before pyannote runs, or after an out-of-memory error)."""
    global _model, _pipe, _compute_type
    _pipe = None
    _model = None
    _compute_type = None
    gc.collect()
    if "torch" in sys.modules:
        import torch
        torch.cuda.empty_cache()
