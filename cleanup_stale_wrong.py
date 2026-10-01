# -*- coding: utf-8 -*-
"""
清理错题档案中的「失配题」（不在当前题库的旧格式题号 / 已下架题）。

背景：GitHub 仓库早期把 wrong_notebook_default_*.json（旧题库格式题号，如
'01-基础-13' 三段式）作为模板提交。新用户 clone 后首次运行，本地没有档案时
系统会自动从 default 模板复制一份，导致错题本里混入大量旧格式题；这些题在
当前题库（四段式题号，如 '01-基础-选-13'）中全部匹配不上，会造成
「今日到期（艾宾浩斯）」数字虚高（到期统计不过滤失配题）。

本脚本会：
1. 扫描 user_data/ 下所有 wrong_notebook_*.json 档案（按科目加载对应题库）；
2. 备份原文件为 .bak_stale_<时间戳>.json；
3. 移除 wrong_questions / seen_question_ids / 每日安排与最近试卷记录中的失配题号；
4. 写回档案（保留你在做的题、错题次数、艾宾浩斯阶段等一切有效数据）。

用法（项目根目录）：
    .venv\\Scripts\\python.exe cleanup_stale_wrong.py
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from core.bank_loader import BankLoader  # noqa: E402
from core.models import SubjectType  # noqa: E402
from core.state_manager import StateManager  # noqa: E402

SUBJ = {
    "数学一": SubjectType.MATH_1,
    "数学二": SubjectType.MATH_2,
    "数学三": SubjectType.MATH_3,
}

_DATA = PROJECT / "user_data"


def _subject_of(name: str):
    for suffix, subj in SUBJ.items():
        if name.endswith(f"_{suffix}"):
            return subj
    return None


def main():
    total_wrong = total_seen = 0
    for p in sorted(_DATA.glob("wrong_notebook_*.json")):
        name = p.stem.replace("wrong_notebook_", "")
        subj = _subject_of(name)
        if subj is None:
            print(f"跳过（无科目后缀，非当前档案格式）: {p.name}")
            continue
        try:
            qlist = BankLoader(subject=subj).load()
        except Exception as e:
            print(f"跳过（题库加载失败）: {p.name} → {e}")
            continue
        valid = {q.id for q in qlist}
        sm = StateManager(data_file=p)
        stale_wrong = [qid for qid in sm.wrong_questions if qid not in valid]
        stale_seen = [qid for qid in sm.historical_seen_ids if qid not in valid]
        stale_assigned = [
            (day, qids) for day, qids in sm._assigned_daily.items()
            if any(q not in valid for q in qids)
        ]
        stale_processed = [
            (day, qids) for day, qids in sm._processed_daily.items()
            if any(q not in valid for q in qids)
        ]
        stale_papers = [
            (i, paper) for i, paper in enumerate(sm.last_papers_qids)
            if any(q not in valid for q in paper)
        ]
        if not (stale_wrong or stale_seen or stale_assigned or stale_processed or stale_papers):
            print(f"无需清理: {p.name}")
            continue
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = p.with_name(f"{p.stem}.bak_stale_{ts}{p.suffix}")
        try:
            shutil.copy2(p, bak)
        except Exception as e:
            print(f"备份失败，跳过: {p.name} → {e}")
            continue
        for qid in stale_wrong:
            sm.wrong_questions.pop(qid, None)
        sm.historical_seen_ids -= set(stale_seen)
        for day, qids in stale_assigned:
            sm._assigned_daily[day] = [q for q in qids if q in valid]
        for day, qids in stale_processed:
            sm._processed_daily[day] = [q for q in qids if q in valid]
        if stale_papers:
            for i, _ in reversed(stale_papers):
                sm.last_papers_qids.pop(i)
        sm.save_state()
        total_wrong += len(stale_wrong)
        total_seen += len(stale_seen)
        print(
            f"清理 {p.name}: 移除错题 {len(stale_wrong)} 道、"
            f"已做记录 {len(stale_seen)} 道、"
            f"安排记录 {len(stale_assigned)} 天、已处理记录 {len(stale_processed)} 天、"
            f"最近试卷 {len(stale_papers)} 份（备份: {bak.name}）"
        )
    print(f"\n完成：共移除失配错题 {total_wrong} 道、失配已做记录 {total_seen} 条。")


if __name__ == "__main__":
    main()
