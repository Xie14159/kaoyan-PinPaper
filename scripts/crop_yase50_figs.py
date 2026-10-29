# -*- coding: utf-8 -*-
"""亚瑟爱数学精编50套（数学二） -> 50 套 × 22 题：逐题裁图 + metadata json + 首页索引。
每套固定 4 页（PDF 第 3 页起）。裁图边界在生成 band 时一次算定，避免二次推算。
"""
import json, re, os, io
import fitz
from PIL import Image

PDF = r"C:\Users\Xie\WPSDrive\1718374671\WPS企业云盘\江西理工大学\我的企业文档\考研备考\考研数学\亚瑟爱数学精选模拟卷\【A4】数二精编50套.pdf"
ROOT = r"D:\考研备考\考研数学\pinhaojuan system\kaoyan-PinPaper"
FIGS = os.path.join(ROOT, "static", "figs")
META = os.path.join(ROOT, "题库资料", "真题数学二", "metadata")
BOOK, ID_BASE, PPS, FIRST, NSETS, NQ = "亚瑟50套", 5000, 4, 2, 50, 22

HDR = re.compile(r"^精选\s*50\s*套\s*[·.．]\s*卷")
FOOT = re.compile(r"第\s*\d+\s*页\s*[，,]\s*共\s*\d+\s*页")
QNUM = re.compile(r"^\s*(?:\(\s*(\d{1,2})\s*\)|(\d{1,2})\s*[.．、]\s*)")
SEC = re.compile(r"^\s*[一二三]\s*[、.．]?\s*(选择题|填空题|解答题)")
CODE = {"选择题": "选", "填空题": "填", "解答题": "解"}
REN = {"选": "x", "填": "t", "解": "j"}

CH = {
    "第一章 函数、极限、连续": ["极限", "lim", "无穷小", "等价无穷小", "连续", "间断", "渐近线", "数列", "无穷大"],
    "第二章 一元函数微分学及其应用": ["导数", "可导", "f′", "极值", "拐点", "单调", "凹凸", "中值定理", "泰勒", "曲率", "洛必达", "切线", "法线", "不等式", "最大值", "最小值"],
    "第三章 一元函数积分学及其应用": ["原函数", "不定积分", "定积分", "反常积分", "变上限", "旋转体", "侧面积", "弧长", "∫", "积分中值", "平均值", "形心", "做功", "引力", "面积", "体积", "水压力"],
    "第四章 多元函数微分学及其应用": ["偏导", "全微分", "可微", "多元", "条件极值", "拉格朗日", "二元", "∂"],
    "第五章 二重积分": ["二重积分", "∬", "极坐标", "累次积分", "积分区域"],
    "第六章 微分方程及其应用": ["微分方程", "通解", "特解", "特征方程", "齐次方程"],
    "第七章 行列式": ["行列式"],
    "第八章 矩阵": ["矩阵", "逆矩阵", "伴随", "秩", "分块"],
    "第九章 向量": ["向量组", "线性相关", "线性无关", "线性表示", "极大无关", "向量"],
    "第十章 线性方程组": ["方程组", "基础解系", "同解", "解的判定"],
    "第十一章 相似矩阵": ["相似", "特征值", "特征向量", "对角化"],
    "第十二章 二次型": ["二次型", "正定", "标准形", "规范形", "合同", "正交变换"],
}
ORDER = ["第五章 二重积分", "第六章 微分方程及其应用", "第十二章 二次型", "第十一章 相似矩阵",
         "第十章 线性方程组", "第九章 向量", "第八章 矩阵", "第七章 行列式",
         "第四章 多元函数微分学及其应用", "第三章 一元函数积分学及其应用",
         "第二章 一元函数微分学及其应用", "第一章 函数、极限、连续"]

def classify(text):
    sub = text[:400]
    best, hits = "第一章 函数、极限、连续", 0
    for ch in ORDER:
        h = sum(1 for k in CH[ch] if k in sub)
        if h > hits:
            best, hits = ch, h
    return best

TYPE_OF = lambda n: "选择题" if n <= 10 else ("填空题" if n <= 16 else "解答题")

def junk(t):
    return bool(HDR.match(t)) or bool(FOOT.search(t)) or "亚瑟精选优质" in t

_ROW_CACHE = {}

def page_rows(pi):
    if pi not in _ROW_CACHE:
        _ROW_CACHE[pi] = rows_of(doc[pi])
    return _ROW_CACHE[pi]

def rows_of(page):
    raw = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            t = "".join(s["text"] for s in ln["spans"])
            if not t.strip() or junk(t):
                continue
            raw.append((fitz.Rect(ln["bbox"]), t))
    raw.sort(key=lambda x: (round(x[0].y0, 1), x[0].x0))
    rows, cur = [], None
    for bb, t in raw:
        if cur is None:
            cur = {"r": fitz.Rect(bb), "parts": [(bb, t)]}
            continue
        ov = min(cur["r"].y1, bb.y1) - max(cur["r"].y0, bb.y0)
        h = min(cur["r"].height, bb.height) or 1
        if ov / h > 0.45:
            cur["r"] |= bb
            cur["parts"].append((bb, t))
        else:
            rows.append(cur)
            cur = {"r": fitz.Rect(bb), "parts": [(bb, t)]}
    if cur:
        rows.append(cur)
    rows.sort(key=lambda r: r["r"].y0)
    return [(r["r"], "".join(p[1] for p in sorted(r["parts"], key=lambda p: p[0].x0))) for r in rows]

doc = fitz.open(PDF)
os.makedirs(FIGS, exist_ok=True)
os.makedirs(META, exist_ok=True)
for f in os.listdir(FIGS):
    if f.startswith("yase_"):
        os.remove(os.path.join(FIGS, f))

bad, made, index = [], 0, []
for k in range(NSETS):
    base = ID_BASE + 1 + k
    pages = [p for p in range(FIRST + PPS * k, FIRST + PPS * (k + 1)) if p < doc.page_count]
    expect, code, seq = 1, "选", []
    for pi in pages:
        rows = rows_of(doc[pi])
        starts = []
        for ri, (rr, txt) in enumerate(rows):
            ms = SEC.match(txt)
            if ms:
                code = CODE[ms.group(1)]
                continue
            mq = QNUM.match(txt)
            if mq:
                num = int(mq.group(1) or mq.group(2))
                if num == expect:
                    starts.append((ri, expect, code))
                    expect += 1
        if not starts:
            if seq and rows:
                r = fitz.Rect(rows[0][0]) | fitz.Rect(rows[-1][0])
                seq[-1]["bands"].append((pi, 30.0, min(r.y1 + 6.0, doc[pi].rect.height - 8.0), rows[0][1]))
            continue
        if starts[0][0] > 0 and seq:
            head = rows[:starts[0][0]]
            r = fitz.Rect(head[0][0]) | fitz.Rect(head[-1][0])
            seq[-1]["bands"].append((pi, max(30.0, r.y0 - 2.0), min(r.y1 + 6.0, rows[starts[0][0]][0].y0 - 4.0),
                                     " ".join(h[1] for h in head)))
        for i, (ri, num, qcode) in enumerate(starts):
            nxt = starts[i + 1][0] if i + 1 < len(starts) else len(rows)
            band = rows[ri:nxt]
            if not band:
                continue
            rect = fitz.Rect(band[0][0]) | fitz.Rect(band[-1][0])
            # 上边界
            floor, j = None, ri - 1
            while j >= 0 and rows[j][0].y1 > rect.y0 - 30.0 and not SEC.match(rows[j][1]):
                j -= 1
            if j + 1 <= ri - 1:
                floor = min(rows[x][0].y0 for x in range(j + 1, ri))
            if floor is not None:
                top = max(30.0, floor - 1.0)
            elif ri == 0:
                top = max(30.0, rect.y0 - 12.0)
            elif SEC.match(rows[ri - 1][1]):
                top = max(30.0, rows[ri - 1][0].y1 + 3.0)   # 紧贴题型标题，标题不进图
            elif (rect.y0 - rows[ri - 1][0].y1) < 26.0:
                top = max(30.0, rows[ri - 1][0].y0 - 1.0)
            else:
                top = max(30.0, rows[ri - 1][0].y1 + 1.0)
            top = min(top, rect.y0)
            # 下边界
            if nxt < len(rows):
                bot = min(rect.y1 + 6.0, rows[nxt][0].y0 - 4.0)
            else:
                bot = rect.y1 + 6.0
            bot = max(bot, rect.y1)

            bot = min(bot, doc[pi].rect.height - 8.0)
            seq.append({"num": num, "code": qcode,
                        "bands": [(pi, top, bot, " ".join(b[1] for b in band))]})
    if len(seq) != NQ:
        bad.append((base, k + 1, len(seq)))
    items = []
    for q in seq:
        ims, texts = [], []
        for (pi, top, bot, txt) in q["bands"]:
            page = doc[pi]
            texts.append(txt)
            clip = fitz.Rect(40, top, min(578, page.rect.width - 12), bot) & page.rect
            pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2.0, 2.0), colorspace=fitz.csGRAY)
            im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
            ims.append(im.point(lambda p: 0 if p < 215 else 255, mode="1"))
        w = max(i.width for i in ims)
        h = sum(i.height for i in ims) + 8 * (len(ims) - 1)
        canvas = Image.new("1", (w, h), 1)
        y = 0
        for i in ims:
            canvas.paste(i, (0, y))
            y += i.height + 8
        name = f"yase_{base}-{REN[q['code']]}{q['num']:02d}.png"
        canvas.save(os.path.join(FIGS, name), optimize=True)
        made += 1
        stem = QNUM.sub("", " ".join(texts), count=1).strip()
        items.append({
            "id": f"{base}-{q['code']}{q['num']:02d}",
            "book": BOOK, "year": "", "question_type": TYPE_OF(q["num"]),
            "tags": [], "stem": stem[:1200], "options": [],
            "chapter": classify(stem), "difficulty": "基础题",
            "stem_img": f"figs/{name}",
        })
    with open(os.path.join(META, f"yase_{base}.json"), "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    index.append({"n": k + 1, "title": f"亚瑟50套·卷{k+1}", "count": len(items)})

with open(os.path.join(META, "_yase_index.json"), "w", encoding="utf-8") as fh:
    json.dump(index, fh, ensure_ascii=False, indent=1)
print("套数", NSETS, "图片", made, "异常套", bad[:10], "题数", sum(i["count"] for i in index))