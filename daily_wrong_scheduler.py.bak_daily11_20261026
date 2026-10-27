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


def _precache_solutions(questions) -> None:
    """为给定题目预生成 AI 名师解析并写入本地缓存（user_data/ai_solutions.json）。

    复用 app.py 的生成管线（Claude 中转生成 + DeepSeek 独立审核 + 原子写缓存）：
    - 缺答案/解析（含"略"占位）的题才走 AI，已有官方答案的题不烧钱；
    - 低并发（max_workers=2）防中转限流；失败不阻断主流程（安排已完成）。
    """
    try:
        from core.ai_solutions import ensure_solutions, needs_solution
        from core.ai_tutor import AITutor
        import json as _json

        cfg = _json.loads(io.open(os.path.join(PROJECT_ROOT, "user_data", "ai_config.json"), encoding="utf-8").read())
    except Exception as ex:
        print(f"预缓存跳过（配置不可读）：{ex}")
        return
    if not (cfg.get("solution_api_key") and cfg.get("solution_base_url")):
        print("预缓存跳过：未配置 AI 名师（user_data/ai_config.json 缺 solution_api_key/solution_base_url）。")
        return

    missing = [q for q in questions if needs_solution(q)]
    if not missing:
        print(f"今日 {len(questions)} 道题均有官方答案/解析，无需 AI 预缓存。")
        return

    tutor = AITutor(
        api_key=cfg["solution_api_key"],
        base_url=cfg["solution_base_url"],
        model=cfg.get("solution_model") or "claude-sonnet-4-5-20250929",
    )
    print(f"开始为今日 {len(missing)} 道缺解析题预生成 AI 答案并缓存…")
    try:
        gen, cached = ensure_solutions(missing, tutor, max_workers=2)
        print(f"预缓存完成：新生成 {gen} 道，复用缓存 {cached} 道。")
    except Exception as ex:
        print(f"预缓存失败（不影响今日安排）：{ex}")


def main() -> int:
    try:
        loader = BankLoader(subject=SUBJECT)
        questions = loader.load()
        chapter_map = {q.id: (q.chapter or "") for q in questions}
        q_by_id = {q.id: q for q in questions}

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

        # 安排的同时预生成 AI 名师解析并缓存（做题/下载解析时秒出，不等生成）
        _assigned_qs = [q_by_id[q] for q in qids if q in q_by_id]
        _precache_solutions(_assigned_qs)
        return 0
    except Exception as ex:  # 定时任务内兜底，不静默失败
        print(f"每日错题安排脚本失败：{ex}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
