"""P0-5: 异常见面化——统一错误日志入口。"""
import logging
import re
import traceback
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_ERROR_LOG = _LOG_DIR / "error.log"

# 只认带时间戳的条目首行；堆栈里出现的 "[...]"（如 File "[...]"）不能被当成新条目
_ENTRY_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")


def log_exc(where: str, e: Exception = None) -> None:
    """记录异常到 logs/error.log（含完整堆栈）。"""
    try:
        with open(_ERROR_LOG, "a", encoding="utf-8") as f:
            import datetime
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {where}: {e}\n")
            if e is not None:
                f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass


def read_recent_errors(n: int = 3) -> list:
    """读取最近n条错误。"""
    try:
        if not _ERROR_LOG.exists():
            return []
        lines = _ERROR_LOG.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        current = []
        for line in reversed(lines):
            if _ENTRY_RE.match(line) and current:
                entries.append("\n".join(reversed(current)))
                current = [line]
            elif _ENTRY_RE.match(line):
                current = [line]
            else:
                current.append(line)
            if len(entries) >= n:
                break
        if current and len(entries) < n:
            entries.append("\n".join(reversed(current)))
        return entries[:n]
    except Exception:
        return []
