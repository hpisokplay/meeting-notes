"""pyannote community-1 speaker diarization from a local model folder (no HF token needed)."""
import gc
import os
from pathlib import Path

from asr import setup_cuda_dlls

MODEL_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "MeetingWorker" / "models" / "pyannote-c1"


def diarize(audio, model_dir=None, sr=16000):
    """audio: float32 mono numpy at 16 kHz. Returns [{"s","e","spk"}]."""
    setup_cuda_dlls()
    import torch
    from pyannote.audio import Pipeline
    pipe = Pipeline.from_pretrained(str(model_dir or MODEL_DIR)).to(torch.device("cuda"))
    try:
        res = pipe({"waveform": torch.from_numpy(audio)[None], "sample_rate": sr})
        ann = getattr(res, "speaker_diarization", res)
        return [{"s": float(t.start), "e": float(t.end), "spk": s}
                for t, _, s in ann.itertracks(yield_label=True)]
    finally:
        del pipe
        gc.collect()
        torch.cuda.empty_cache()
