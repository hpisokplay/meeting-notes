"""Ask a local ComfyUI to release the GPU memory it keeps after generating. Stdlib only, never raises."""
import json
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8188"
TIMEOUT_S = 5


def url_from_config(cfg):
    """Optional "comfyui" key in config.json: absent = default URL; "" or false disables."""
    v = cfg.get("comfyui", True)
    if v is True:
        return DEFAULT_URL
    return v.strip().rstrip("/") if isinstance(v, str) else ""


def free_comfyui(base_url, urlopen=urllib.request.urlopen):
    """-> "freed" | "busy" (queue not empty: left alone) | "unreachable" | "disabled"."""
    if not base_url:
        return "disabled"
    base = base_url.rstrip("/")
    try:
        with urlopen(base + "/queue", timeout=TIMEOUT_S) as r:
            q = json.loads(r.read().decode("utf-8"))
        # free only when the answer positively shows BOTH queues empty
        run, pend = (q.get("queue_running"), q.get("queue_pending")) if isinstance(q, dict) else (None, None)
        if not isinstance(run, list) or not isinstance(pend, list):
            return "unreachable"
        if run or pend:
            return "busy"
        req = urllib.request.Request(
            base + "/free", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"))
        with urlopen(req, timeout=TIMEOUT_S) as r:
            return "freed" if 200 <= getattr(r, "status", 200) < 300 else "unreachable"
    except Exception:
        return "unreachable"
