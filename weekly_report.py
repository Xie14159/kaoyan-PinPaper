#!/usr/bin/env python3
"""拼好卷使用日志周报：读 logs/usage.jsonl，输出最近7天统计。"""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "usage.jsonl")

def main():
    if not os.path.exists(LOG):
        print("（暂无使用日志）")
        return
    cutoff = datetime.now() - timedelta(days=7)
    by_day = defaultdict(lambda: {"correct": 0, "wrong": 0, "qids": set()})
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                ts = datetime.fromisoformat(r["ts"])
            except Exception:
                continue
            if ts < cutoff:
                continue
            day = ts.strftime("%m-%d")
            kind = r.get("kind", "?")
            by_day[day][kind] = by_day[day].get(kind, 0) + 1
            by_day[day]["qids"].add(r.get("qid", ""))

    total = sum(len(v["qids"]) for v in by_day.values())
    correct = sum(v.get("correct", 0) for v in by_day.values())
    wrong = sum(v.get("wrong", 0) for v in by_day.values())
    graded = correct + wrong
    acc = f"{correct/graded*100:.0f}%" if graded else "—"

    print(f"=== 近7天使用周报 ===")
    print(f"总判分: {graded} 次 · 做对 {correct} · 又错 {wrong} · 正确率 {acc}")
    print()
    for day in sorted(by_day):
        v = by_day[day]
        c, w = v.get("correct", 0), v.get("wrong", 0)
        g = c + w
        a = f"{c/g*100:.0f}%" if g else "—"
        print(f"  {day}: {g:3d} 题 (对{c} 错{w} 正确率{a})")

if __name__ == "__main__":
    main()
