# -*- coding: utf-8 -*-
"""只读验证：每日错题 target=11（不写盘，不动今日安排）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.bank_loader import BankLoader
from core.models import SubjectType
from core.state_manager import StateManager

SUBJECT = SubjectType.MATH_2
loader = BankLoader(subject=SUBJECT)
questions = loader.load()
chapter_map = {q.id: (q.chapter or "") for q in questions}

mgr = StateManager(username="local", subject=SUBJECT)
processed = mgr.get_processed_today()
assigned = mgr.get_assigned_today()
print("今日已安排(读):", len(assigned), "今日已处理(读):", len(processed))

qids = mgr.select_daily_wrong(target=11, exclude_ids=processed, chapter_of=chapter_map)
print("select_daily_wrong(target=11) 返回:", len(qids))
print("是否<=11:", len(qids) <= 11)
print("是否去重:", len(set(qids)) == len(qids))
print("章节覆盖数:", len({chapter_map.get(q, '') for q in qids}))
for q in qids:
    rec = mgr.wrong_questions.get(q)
    print(" ", q, chapter_map.get(q, ""), "错次=", rec.wrong_count if rec else "?",
          "stage=", rec.review_stage if rec else "?")

# 写盘状态未被改动
assert mgr.get_assigned_today() == assigned, "安排被改动！"
print("OK 只读验证通过，未改动今日安排")
