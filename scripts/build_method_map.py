"""把重点错题二级方法标签从 103 道候选池扩到全库。

规则：
- 已人工核过的 103 道（生产 `_kp_method_map.json`）原样保留，作为权威覆盖层。
- 其余题目用 v1 规则分类器按一级簇 + 题干关键词打二级方法标签。
- 标签必须命中 `ALLOWED` 白名单，否则回退到该簇的“综合”二级方法。
- 证明题不打标签（复盘页本就排除）。

运行：.venv/Scripts/python.exe scripts/build_method_map.py
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import core.bank_loader as bl  # noqa: E402
from core.models import SubjectType  # noqa: E402
from core.question_utils import is_proof_question  # noqa: E402

META = BASE / "题库资料" / "真题数学二" / "metadata"
PROD_MAP = META / "_kp_method_map.json"


# ---------- 与 v2 脚本一致的合法二级方法白名单 ----------
ALLOWED = {
    "01-未定式与等价替换": {"等价无穷小", "洛必达", "泰勒展开", "幂指函数(1^∞)", "变限积分极限", "根式·有理化", "未定式(综合)"},
    "01-数列极限与单调有界": {"单调有界准则", "夹逼准则", "定积分定义(数列和)", "概念辨析", "恒等变形", "数列极限(综合)"},
    "01-连续与间断点": {"间断点分类", "连续性判定", "零点定理"},
    "02-导数定义与可导性": {"导数定义求极限", "分段点可导性", "绝对值可导性", "可导性判定"},
    "02-复合隐函数参数方程求导": {"复合函数链式", "隐函数求导", "参数方程求导", "反函数求导", "相关变化率", "高阶导数"},
    "02-凹凸拐点与渐近线": {"凹凸性判定", "拐点判定", "渐近线"},
    "02-单调性与极值最值": {"单调区间", "极值判定", "最值", "方程根个数"},
    "03-定积分计算与对称性": {"定积分计算(综合)", "换元法", "分部积分", "对称性奇偶性", "周期性平移", "变限积分求导", "定积分定义(数列和)", "原函数·平均值", "平均值·零点"},
    "03-面积体积弧长旋转体": {"面积(直角坐标)", "面积(极坐标)", "旋转体体积", "弧长", "几何应用(综合)"},
    "03-反常积分敛散": {"判敛", "反常积分计算", "含参讨论"},
    "03-积分不等式与估值": {"比较大小", "估值不等式"},
    "03-物理应用": {"做功", "质量压力", "质心", "物理应用(综合)"},
    "04-偏导与全微分": {"一阶偏导", "二阶偏导", "高阶偏导", "全微分", "可微性判定"},
    "04-复合与隐函数求导": {"复合函数(多元)", "隐函数(多元)", "二阶偏导(多元)", "复合函数链式"},
    "04-极值与条件极值": {"无条件极值", "条件极值", "最值(有界闭域)", "全微分求原函数"},
    "05-直角坐标计算": {"直角坐标累次积分", "交换次序", "区域分割", "对称性", "换元·三角代换", "换元(u=x+y,v=xy)", "分段函数积分"},
    "05-极坐标换元": {"极坐标换元", "极坐标+对称性", "含参积分·极限", "含参积分·微分方程"},
    "06-一阶方程": {"可分离变量", "齐次方程", "一阶线性", "伯努利", "全微分方程", "一阶方程(综合)"},
    "06-二阶常系数": {"齐次(特征根)", "非齐次(待定系数)", "欧拉方程", "积分方程", "三阶常系数"},
    "06-几何与物理应用": {"几何建模", "物理建模", "几何/物理建模"},
    "08-秩": {"秩的性质·不等式", "秩的分块性质", "秩与方程组", "秩的计算"},
    "08-矩阵运算与幂": {"矩阵幂", "矩阵运算"},
    "08-初等变换与分块": {"初等变换", "分块矩阵"},
    "09-相关性与秩": {"线性相关判定", "极大无关组", "秩与相关性"},
    "10-通解结构": {"齐次通解", "非齐次通解", "解的结构", "解的结构(伴随)", "基础解系"},
    "11-相似对角化": {"可对角化判定", "相似性质", "求P", "实对称正交对角化", "相似对角化"},
    "11-特征值与特征向量": {"求特征值", "特征值性质", "求特征向量"},
    "12-标准形与规范形": {"标准形(配方法)", "惯性指数", "正定判定", "合同"},
    "12-正交变换化标准形": {"正交变换"},
}

# v1 规则输出名 -> 白名单名（历史命名差异）
NORMALIZE = {
    "根式/有理化": "根式·有理化",
    "对称性/奇偶性": "对称性奇偶性",
    "曲率": "凹凸性判定",
    "递推数列": "单调有界准则",
    "压力": "质量压力",
    "相似性质/求参": "相似性质",
    "估值/不等式": "估值不等式",
}

# 每个一级簇的“综合”兜底二级方法
FALLBACK = {
    "01-未定式与等价替换": "未定式(综合)",
    "01-数列极限与单调有界": "数列极限(综合)",
    "01-连续与间断点": "连续性判定",
    "02-导数定义与可导性": "可导性判定",
    "02-复合隐函数参数方程求导": "复合函数链式",
    "02-凹凸拐点与渐近线": "凹凸性判定",
    "02-单调性与极值最值": "单调区间",
    "03-定积分计算与对称性": "定积分计算(综合)",
    "03-面积体积弧长旋转体": "几何应用(综合)",
    "03-反常积分敛散": "反常积分计算",
    "03-积分不等式与估值": "比较大小",
    "03-物理应用": "物理应用(综合)",
    "04-偏导与全微分": "一阶偏导",
    "04-复合与隐函数求导": "复合函数(多元)",
    "04-极值与条件极值": "无条件极值",
    "05-直角坐标计算": "直角坐标累次积分",
    "05-极坐标换元": "极坐标换元",
    "06-一阶方程": "一阶方程(综合)",
    "06-二阶常系数": "齐次(特征根)",
    "06-几何与物理应用": "几何/物理建模",
    "08-秩": "秩的计算",
    "08-矩阵运算与幂": "矩阵运算",
    "08-初等变换与分块": "初等变换",
    "09-相关性与秩": "线性相关判定",
    "10-通解结构": "解的结构",
    "11-相似对角化": "相似对角化",
    "11-特征值与特征向量": "特征值性质",
    "12-标准形与规范形": "标准形(配方法)",
    "12-正交变换化标准形": "正交变换",
}


def m(stem: str, *kws: str) -> bool:
    return any(k in stem for k in kws)


def rule_method(l1: str, stem: str) -> str:
    """与 _kp_method_v1.py 完全一致的规则分类器。"""
    s = stem
    if l1 == "01-未定式与等价替换":
        if m(s, "1^{\\infty}", ")^{\\frac{1}", ")^{1/", "(1+") and m(s, "lim"):
            return "幂指函数(1^∞)"
        if m(s, "等价无穷小", "是等价无穷小", "与 $c", "等价"):
            return "等价无穷小"
        if m(s, "洛必达"):
            return "洛必达"
        if m(s, "泰勒", "展开"):
            return "泰勒展开"
        if m(s, "变限积分", "\\int_{0}^{x", "\\int_0^x"):
            return "变限积分极限"
        if m(s, "根号", "\\sqrt") and m(s, "lim"):
            return "根式/有理化"
        return "未定式(综合)"
    if l1 == "01-数列极限与单调有界":
        if m(s, "夹逼"):
            return "夹逼准则"
        if m(s, "单调", "有界"):
            return "单调有界准则"
        if m(s, "\\sum") and m(s, "lim"):
            return "定积分定义(数列和)"
        if m(s, "x_{n+1}", "递推", "a_{n+1}"):
            return "递推数列"
        return "数列极限(综合)"
    if l1 == "01-连续与间断点":
        if m(s, "间断点", "间断"):
            return "间断点分类"
        if m(s, "零点", "根"):
            return "零点定理"
        return "连续性判定"
    if l1 == "02-导数定义与可导性":
        if m(s, "分段", "x>0", "x \\leqslant 0", "x\\le0"):
            return "分段点可导性"
        if m(s, "|f(x)|", "绝对值"):
            return "绝对值可导性"
        if m(s, "f'(0)") and m(s, "lim"):
            return "导数定义求极限"
        return "可导性判定"
    if l1 == "02-复合隐函数参数方程求导":
        if m(s, "参数方程"):
            return "参数方程求导"
        if m(s, "隐函数"):
            return "隐函数求导"
        if m(s, "反函数"):
            return "反函数求导"
        if m(s, "速率", "变化率"):
            return "相关变化率"
        if m(s, "n 阶", "n阶", "阶导数"):
            return "高阶导数"
        return "复合函数链式"
    if l1 == "02-凹凸拐点与渐近线":
        if m(s, "曲率"):
            return "曲率"
        if m(s, "渐近线"):
            return "渐近线"
        if m(s, "拐点"):
            return "拐点判定"
        return "凹凸性判定"
    if l1 == "02-单调性与极值最值":
        if m(s, "最值", "最大值", "最小值", "最长", "最短"):
            return "最值"
        if m(s, "极值"):
            return "极值判定"
        if m(s, "零点", "根的个数", "实根"):
            return "方程根个数"
        return "单调区间"
    if l1 == "03-定积分计算与对称性":
        if m(s, "交换次序", "积分次序", "交换积分"):
            return "交换次序"
        if m(s, "变限积分", "\\int_{0}^{x", "\\int_0^x") and m(s, "求导", "导数"):
            return "变限积分求导"
        if m(s, "对称", "奇偶", "奇函数", "偶函数"):
            return "对称性/奇偶性"
        if m(s, "周期"):
            return "周期性平移"
        if m(s, "\\sum") and m(s, "lim"):
            return "定积分定义(数列和)"
        if m(s, "分部积分", "分部"):
            return "分部积分"
        if m(s, "换元", "令 $"):
            return "换元法"
        return "定积分计算(综合)"
    if l1 == "03-面积体积弧长旋转体":
        if m(s, "弧长"):
            return "弧长"
        if m(s, "旋转体", "绕", "旋转一周", "旋转所得"):
            return "旋转体体积"
        if m(s, "面积") and m(s, "极坐标", "r =", "r="):
            return "面积(极坐标)"
        if m(s, "面积", "围成", "所围"):
            return "面积(直角坐标)"
        return "几何应用(综合)"
    if l1 == "03-反常积分敛散":
        if m(s, "收敛", "发散", "敛散"):
            return "判敛"
        return "反常积分计算"
    if l1 == "03-积分不等式与估值":
        if m(s, "估值", "不等式", "证明"):
            return "估值/不等式"
        return "比较大小"
    if l1 == "03-物理应用":
        if m(s, "功", "做功"):
            return "做功"
        if m(s, "压力"):
            return "压力"
        if m(s, "质心", "形心"):
            return "质心"
        return "物理应用(综合)"
    if l1 == "04-偏导与全微分":
        if m(s, "全微分"):
            return "全微分"
        if m(s, "二阶", "\\frac{\\partial^2"):
            return "二阶偏导"
        return "一阶偏导"
    if l1 == "04-复合与隐函数求导":
        if m(s, "隐函数"):
            return "隐函数(多元)"
        if m(s, "二阶", "\\frac{\\partial^2"):
            return "二阶偏导(多元)"
        return "复合函数(多元)"
    if l1 == "04-极值与条件极值":
        if m(s, "条件极值", "约束", "拉格朗日"):
            return "条件极值"
        if m(s, "最值", "最长", "最短", "最大", "最小"):
            return "最值(有界闭域)"
        return "无条件极值"
    if l1 == "05-直角坐标计算":
        if m(s, "交换次序", "积分次序"):
            return "交换次序"
        if m(s, "对称", "奇偶"):
            return "对称性"
        return "直角坐标累次积分"
    if l1 == "05-极坐标换元":
        if m(s, "对称", "奇偶"):
            return "极坐标+对称性"
        return "极坐标换元"
    if l1 == "06-一阶方程":
        if m(s, "可分离", "分离变量"):
            return "可分离变量"
        if m(s, "一阶线性", "y' +", "y^{\\prime} +"):
            return "一阶线性"
        if m(s, "齐次"):
            return "齐次方程"
        if m(s, "伯努利"):
            return "伯努利"
        return "一阶方程(综合)"
    if l1 == "06-二阶常系数":
        if m(s, "非齐次", "特解"):
            return "非齐次(待定系数)"
        return "齐次(特征根)"
    if l1 == "06-几何与物理应用":
        return "几何/物理建模"
    if l1 == "08-矩阵运算与幂":
        if m(s, "A^{99}", "A^{n}", "幂"):
            return "矩阵幂"
        return "矩阵运算"
    if l1 == "08-初等变换与分块":
        if m(s, "分块"):
            return "分块矩阵"
        return "初等变换"
    if l1 == "11-相似对角化":
        if m(s, "实对称"):
            return "实对称正交对角化"
        if m(s, "可对角化", "能否对角化", "不一定"):
            return "可对角化判定"
        if m(s, "相似于", "P^{-1}AP"):
            return "相似性质/求参"
        return "相似对角化"
    if l1 == "12-标准形与规范形":
        if m(s, "惯性指数"):
            return "惯性指数"
        if m(s, "正定", "半正定"):
            return "正定判定"
        return "标准形(配方法)"
    if l1 == "12-正交变换化标准形":
        return "正交变换"
    return FALLBACK.get(l1, "定积分计算(综合)")


def extract_py_dict(src: str, var: str):
    m = re.search(rf"{var} = (\{{.*?\n\}})", src, re.S)
    if not m:
        return {}
    return ast.literal_eval(m.group(1))


def main() -> int:
    app_src = (BASE / "app.py").read_text(encoding="utf-8")
    cap = extract_py_dict(app_src, "_FOCUS_KP_CAP")
    fix = extract_py_dict(app_src, "_FOCUS_KP_FIX")

    curated = json.loads(PROD_MAP.read_text(encoding="utf-8"))
    kp_index = json.loads((META / "_kp_index.json").read_text(encoding="utf-8"))

    bank = {q.id: q for q in bl.BankLoader(subject=SubjectType.MATH_2).load()}
    print(f"题库总数: {len(bank)}; 已人工核: {len(curated)}; kp_index: {len(kp_index)}")

    out = dict(curated)  # 权威层不动
    added = 0
    skipped_proof = 0
    skipped_outside_cap = 0
    fallback_count = Counter()
    invalid = []

    for qid, q in bank.items():
        if qid in out:
            continue
        if is_proof_question(q):
            skipped_proof += 1
            continue
        # 一级簇：优先 _FOCUS_KP_FIX，再 kp_index，再按题号前缀兜底
        if qid in fix and fix[qid] is not None:
            l1 = fix[qid]
        else:
            l1 = kp_index.get(qid) or f"{str(qid).split('-')[0]}-综合与其他"
        if l1 not in cap:
            skipped_outside_cap += 1
            continue
        stem = str(getattr(q, "stem", "") or "")
        l2 = rule_method(l1, stem)
        l2 = NORMALIZE.get(l2, l2)
        if l2 not in ALLOWED.get(l1, set()):
            l2 = FALLBACK.get(l1, "定积分计算(综合)")
            fallback_count[l1] += 1
        out[qid] = f"{l1}::{l2}"
        added += 1

    # 校验：所有值必须合法
    bad = [qid for qid, v in out.items() if v.split("::")[0] not in ALLOWED or v.split("::")[1] not in ALLOWED.get(v.split("::")[0], set())]
    if bad:
        print(f"[FATAL] {len(bad)} 条非法标签，示例: {bad[:5]}")
        return 1

    PROD_MAP.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"写入 {PROD_MAP.relative_to(BASE)}: 共 {len(out)} 道（新增自动 {added}，保留人工 {len(curated)}）")
    print(f"跳过: 证明题 {skipped_proof}，不在白名单簇 {skipped_outside_cap}")
    if fallback_count:
        print("回退到综合二级方法的簇分布:")
        for k, n in fallback_count.most_common():
            print(f"  {k}: {n}")

    # 分布统计
    dist = Counter(v for v in out.values())
    print(f"二级方法总数: {len(dist)}")
    singleton = [(k, n) for k, n in dist.items() if n == 1]
    print(f"全库下只有 1 道题的二级方法: {len(singleton)}")
    for k, _ in sorted(singleton)[:15]:
        print(f"  · {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
