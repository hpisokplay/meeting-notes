"""Spawn targets for ChildRunner tests (must be importable by the child)."""
import os
import time


def ok(conn, task, path, kw):
    import numpy as np
    n = len(np.load(path))
    if task == "asr":
        conn.send(("progress", 0.5))
        conn.send(("progress", 1.0))
        conn.send(("ok", [[0.0, 1.0, "hi"]]))
    else:
        conn.send(("ok", {"task": task, "kw": kw, "n": n}))
    conn.close()


def oom(conn, task, path, kw):
    conn.send(("err", "RuntimeError", "CUDA failed with error out of memory"))
    conn.close()


def crash(conn, task, path, kw):
    os._exit(3)


def hang(conn, task, path, kw):
    time.sleep(120)
