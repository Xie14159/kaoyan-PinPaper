# -*- coding: utf-8 -*-
"""
每日错题自动安排脚本 —— 由豆包定时任务每天 07:00 调用。
确保"今日 10 道错题安排"已生成（考点轮转覆盖尽量多章节），幂等：当天已安排则跳过。
用法：.venv\\Scripts\\python.exe daily_wrong_scheduler.py
"""
import io
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# 强制 UTF-8 输出：Windows 定时任务无控制台（GBK 默认），中文 print 会 UnicodeEncodeError。
# 守卫兼容 sys.stdout 为 None / 无 buffer / 不支持 reconfigure 的各种情况。
for _s in (sys.stdout, sys.stderr):
    if _s is None:
        continue
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    elif getattr(_s, "buffer", None) is not None:
        try:
            _wrapped = io.TextIOWrapper(_s.buffer, encoding="utf-8")
            if _s is sys.stdout:
                sys.stdout = _wrapped
            elif _s is sys.stderr:
                sys.stderr = _wrapped
        except Exception:
            pass

from core.bank_loader import BankLoader  # noqa: E402
from core.models import SubjectType  # noqa: E402
from core.state_manager import StateManager  # noqa: E402

SUBJECT = SubjectType.MATH_2  # 数二考生
TARGET = 10


def main() -> int:
    try:
        loader = BankLoader(subject=SUBJECT)
        questions = loader.load()
        chapter_map = {q.id: (q.chapter or "") for q in questions}

        mgr = StateManager(username="local", subject=SUBJECT)
        if mgr.get_assigned_today():
            n = len(mgr.get_assigned_today())
            print(f"今日错题已安排（{n} 道），跳过。")
            return 0

        processed = mgr.get_processed_today()
        qids = mgr.select_daily_wrong(target=TARGET, exclude_ids=processed, chapter_of=chapter_map)
        if not qids:
            print("今日无可用错题（错题本为空或全部已处理），未生成安排。")
            return 0

        mgr.mark_assigned_today(qids)
        chapters = {chapter_map.get(q, "") for q in qids}
        print(f"已安排 {len(qids)} 道错题，覆盖 {len(chapters)} 个考点章节：")
        for q in qids:
            print(f"  {q}  [{chapter_map.get(q, '')}]")
        return 0
    except Exception as ex:  # 定时任务内兜底，不静默失败
        print(f"每日错题安排脚本失败：{ex}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
