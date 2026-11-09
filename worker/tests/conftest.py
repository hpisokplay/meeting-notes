import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _no_real_comfyui(monkeypatch):
    """Tests must never talk to a ComfyUI that happens to run on this machine."""
    import worker
    monkeypatch.setattr(worker, "COMFY_URL", "")
