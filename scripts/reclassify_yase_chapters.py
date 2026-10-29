# -*- coding: utf-8 -*-
"""用加权关键词给亚瑟50套重判章节（读现成的 stem，不重跑裁图）。默认 dry-run。"""
import json, re, sys
from pathlib import Path
from collections import Counter

META = Path(r"D:\考研备考\考研数学\pinhaojuan system\kaoyan-PinPaper\题库资料\真题数学二\metadata")

STRONG = {
 "第一章 函数、极限、连续": ["极限", "无穷小", "等价无穷小", "间断点", "渐近线", "夹逼", "无穷大", "数列极限"],
 "第二章 一元函数微分学及其应用": ["导数", "可导", "极值", "拐点", "凹", "单调", "中值定理", "泰勒", "麦克劳林", "曲率", "洛必达", "切线", "法线", "罗尔", "拉格朗日余项"],
 "第三章 一元函数积分学及其应用": ["不定积分", "定积分", "反常积分", "原函数", "变上限", "旋转体", "侧面积", "弧长", "形心", "做功", "引力", "水压力", "积分中值", "平均值", "分部积分", "换元积分"],
 "第四章 多元函数微分学及其应用": ["偏导", "全微分", "二元函数", "条件极值", "拉格朗日乘数", "可微"],
 "第五章 二重积分": ["二重积分", "累次积分", "积分区域", "极坐标", "交换积分次序", "∬"],
 "第六章 微分方程及其应用": ["微分方程", "特征方程", "齐次方程", "伯努利", "积分因子", "特解", "通解"],
 "第七章 行列式": ["行列式", "余子式", "代数余子式", "范德蒙"],
 "第八章 矩阵": ["矩阵", "逆矩阵", "伴随矩阵", "分块矩阵", "初等变换", "矩阵的秩", "可逆矩阵"],
 "第九章 向量": ["向量组", "线性相关", "线性无关", "极大线性无关组", "线性表示", "向量空间"],
 "第十章 线性方程组": ["线性方程组", "基础解系", "增广矩阵", "同解", "无穷多解", "非齐次"],
 "第十一章 相似矩阵": ["相似", "特征值", "特征向量", "对角化", "特征多项式"],
 "第十二章 二次型": ["二次型", "正定", "标准形", "规范形", "合同", "惯性指数", "正交变换"],
}
WEAK = {
 "第一章 函数、极限、连续": ["lim", "连续", "∞", "x→"],
 "第二章 一元函数微分学及其应用": ["不等式", "最大值", "最小值", "证明"],
 "第三章 一元函数积分学及其应用": ["∫", "dx", "面积", "体积"],
 "第四章 多元函数微分学及其应用": ["∂", "z=", "x,y"],
 "第五章 二重积分": ["dσ", "∫∫"],
 "第六章 微分方程及其应用": ["y′", "y″", "dy"],
 "第七章 行列式": ["det"],
 "第八章 矩阵": ["A=", "秩"],
 "第九章 向量": ["向量", "α", "β"],
 "第十章 线性方程组": ["方程组", "解"],
 "第十一章 相似矩阵": ["λ", "P⁻¹AP"],
 "第十二章 二次型": ["f(x,y,z)", "xᵀAx"],
}
ORDER = ["第五章 二重积分", "第六章 微分方程及其应用", "第十二章 二次型", "第十一章 相似矩阵",
         "第十章 线性方程组", "第九章 向量", "第八章 矩阵", "第七章 行列式",
         "第四章 多元函数微分学及其应用", "第三章 一元函数积分学及其应用",
         "第二章 一元函数微分学及其应用", "第一章 函数、极限、连续"]

def classify(text: str):
    t = text.replace(" ", "")
    best, best_s, best_n = "", -1, 0
    for ch in ORDER:
        s = 3 * sum(1 for k in STRONG[ch] if k.replace(" ", "") in t) \
            + 1 * sum(1 for k in WEAK[ch] if k.replace(" ", "") in t)
        n = sum(1 for k in STRONG[ch] if k.replace(" ", "") in t)
        if s > best_s or (s == best_s and n > best_n):
            best, best_s, best_n = ch, s, n
    return best if best_s > 0 else ""

def main(write=False):
    dist, changed, empty, samples = Counter(), 0, 0, []
    for jf in sorted(META.glob("yase_5*.json")):
        items = json.loads(jf.read_text(encoding="utf-8"))
        for q in items:
            ch = classify(q.get("stem", ""))
            if not ch:
                empty += 1
            elif ch != q.get("chapter"):
                changed += 1
                q["chapter"] = ch
            dist[q.get("chapter") or "(留空)"] += 1
            if len(samples) < 12 and ch:
                samples.append((q["id"], ch, q["stem"][:46]))
        if write:
            jf.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tot = sum(dist.values())
    print(f"共 {tot} 题；改判 {changed}；无命中 {empty}")
    for k, v in dist.most_common():
        print(f"  {v:4d}  {v/tot*100:5.1f}%  {k}")
    print("\n抽样：")
    for i, ch, s in samples:
        print(f"  {i}  {ch}  | {s}")

if __name__ == "__main__":
    main(write="--write" in sys.argv)