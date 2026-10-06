"""pyannote community-1 speaker diarization from a local model folder (no HF token needed)."""
import gc
from pathlib import Path

from asr import setup_cuda_dlls

MODEL_DIR = Path.home() / "MeetingWorker" / "models" / "pyannote-c1"  # %USERPROFILE%, not MSIX-virtualized


def diarize(audio, model_dir=None, sr=16000, device="cuda"):
    """audio: float32 mono numpy at 16 kHz. Returns [{"s","e","spk"}].
    device="cpu" is the worker's fallback when the GPU stays full (slower)."""
    setup_cuda_dlls()
    import torch
    from pyannote.audio import Pipeline
    pipe = None
    try:
        pipe = Pipeline.from_pretrained(str(model_dir or MODEL_DIR))
        pipe.to(torch.device(device))
        res = pipe({"waveform": torch.from_numpy(audio)[None], "sample_rate": sr})
        ann = getattr(res, "speaker_diarization", res)
        return [{"s": float(t.start), "e": float(t.end), "spk": s}
                for t, _, s in ann.itertracks(yield_label=True)]
    finally:
        pipe = None  # also on out-of-memory while moving to the GPU: free what was allocated
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
