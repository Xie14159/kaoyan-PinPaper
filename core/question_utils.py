# -*- coding: utf-8 -*-
"""题目工具函数：识别证明题等题型子类。"""
from __future__ import annotations

import re

from .models import QuestionItem, QuestionType

# 开头强特征：直接判定为证明题
_PROOF_HEAD_RE = re.compile(r"^\s*(证明|试证|求证|证[:：])")

# 开头是纯计算题（非证明主导）
_NON_PROOF_HEAD_RE = re.compile(r"^\s*(计算|求|解方程|讨论|判断|设.*求|已知.*求)")


def is_proof_question(q: QuestionItem) -> bool:
    """判断一道题是否属于"证明题"（用于错题复习时降权）。

    规则（按优先级）：
    1. 选择题/填空题直接 False（不看题干，避免"下列关于证明题的说法"误判）
    2. question_type 字段含"证明" → True
    3. 题干开头是"证明/试证/求证/证：" → True
    4. 题干前 200 字内出现"证明"，且前 100 字内没有"求"字，
       且开头不是"计算/求/解/讨论/判断"这类纯计算主导 → True
       （覆盖"设 f(x)...，证明：xxx"这种结构；排除"求极限并证明存在"这种计算主导题）
    5. 其余 False

    注意：这个识别故意偏松不偏紧——误把几道综合计算题当证明题，
    后果只是它们在每日错题里排后一点，不会消失；
    漏判证明题则违背降权目的。所以宁可错判几道，不可漏判。
    """
    if q is None:
        return False

    qt = q.question_type
    if isinstance(qt, QuestionType):
        qt_val = qt.value
    else:
        qt_val = str(qt or "")

    # 1. 选择/填空直接 False
    if qt == QuestionType.CHOICE or qt == QuestionType.FILL_BLANK:
        return False
    if "选择" in qt_val or "填空" in qt_val:
        return False

    # 2. 题型字段直接命中
    if "证明" in qt_val:
        return True

    stem = (q.stem or "").strip()
    if not stem:
        return False

    # 3. 开头强特征
    if _PROOF_HEAD_RE.match(stem):
        return True

    # 4. 前 200 字内有"证明"，且前 50 字无"求"（第二问才"求"的证明题不误杀），
    #    且开头不是纯计算题
    if "证明" in stem[:200]:
        if "求" in stem[:50]:
            return False
        if _NON_PROOF_HEAD_RE.match(stem):
            return False
        return True

    return False
