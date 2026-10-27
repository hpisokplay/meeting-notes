"""Claude API 客戶端（只給「完整整理」用；其他 AI 動作仍走 claude CLI / Max）。

金鑰放在檔案裡（預設 /run/meetings/anthropic.key，測試可用環境變數
MEETINGS_ANTHROPIC_KEY_FILE 覆寫）。金鑰不進 log、不進例外訊息、不進 git。
`anthropic` 套件在 call_api 內才 import，沒裝套件時其他功能不受影響。
"""
from __future__ import annotations

import os

REPORT_API_MODEL = "claude-opus-5-5"
MAX_TOKENS = 32000
TOOL_NAME = "submit_report"
_DEFAULT_KEY_FILE = "/run/meetings/anthropic.key"


class ApiError(Exception):
    """API 呼叫失敗。訊息只含例外類別名與 HTTP 狀態，不含金鑰或請求內容。"""


def read_key() -> str | None:
    path = os.environ.get("MEETINGS_ANTHROPIC_KEY_FILE", _DEFAULT_KEY_FILE)
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            key = f.read()
    except OSError:
        return None
    key = key.strip().lstrip("\ufeff").strip()
    return key or None


def api_enabled() -> bool:
    return read_key() is not None


def _safe_error(exc: BaseException) -> ApiError:
    status = getattr(exc, "status_code", None)
    msg = type(exc).__name__ + (f" (HTTP {status})" if status else "")
    return ApiError(msg)


def call_api(system: str, user: str, schema: dict, *, client=None, timeout: float = 600) -> dict:
    err: ApiError | None = None
    try:
        if client is None:
            key = read_key()
            if key is None:
                raise ApiError("API 金鑰未設定")
            import anthropic  # 延遲 import：沒裝套件時不影響其他功能

            client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)
        tool = {
            "name": TOOL_NAME,
            "description": "提交結構化的完整整理結果。",
            "input_schema": schema,
        }
        with client.messages.stream(
            model=REPORT_API_MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
        ) as stream:
            msg = stream.get_final_message()
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise ApiError("輸出被截斷（max_tokens）")
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "type", None) == "tool_use":
                result = block.input
                if not isinstance(result, dict):
                    raise ApiError("結果不是物件")
                return result
        raise ApiError("回應沒有 tool_use 區塊")
    except ApiError as e:
        err = e
    except Exception as e:  # noqa: BLE001 — SDK 例外一律改包，避免洩漏內容
        err = _safe_error(e)
    raise err from None
