# -*- coding: utf-8 -*-
"""贾基85套卷 逐题裁图 v2：
每题的截图范围 = 该题起始行 到 下一题起始行 之间的所有行（含续行），
上边界取"上一行文字的底部+1"，因此既能带上高公式（分子、积分上限），
又不会把上一题的最后一行裁进来。
"""
import re, os, io
import fitz
from PIL import Image

PDF = r"C:\Users\Xie\Downloads\[A4][数学二][紧凑版] 贾基八十五套卷(1)138072699544247888.1ea9d86c82b1d89.pdf"
OUT = r"D:\考研备考\考研数学\pinhaojuan system\kaoyan-PinPaper\static\figs"
HDR = re.compile(r"^\s*(\d{4})\s*([^\s第]{2,8}?卷)\s*第\s*(\d+)\s*套")
FOOT = re.compile(r"第\s*\d+\s*页\s*[（(]\s*共\s*\d+\s*页\s*[)）]")
QNUM = re.compile(r"^\s*(\d{1,2})\s*[.．]\s*")
SEC = re.compile(r"^\s*[一二三]\s*[、.]?\s*(选择题|填空题|解答题)")
CODE = {"选择题": "选", "填空题": "填", "解答题": "解"}
REN = {"选": "x", "填": "t", "解": "j"}
doc = fitz.open(PDF)

def junk(t):
    return bool(HDR.search(t)) or bool(FOOT.search(t)) or "启用前" in t

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
            cur = {"r": fitz.Rect(bb), "parts": [(bb, t)]}; continue
        ov = min(cur["r"].y1, bb.y1) - max(cur["r"].y0, bb.y0)
        h = min(cur["r"].height, bb.height) or 1
        if ov / h > 0.45:
            cur["r"] |= bb; cur["parts"].append((bb, t))
        else:
            rows.append(cur); cur = {"r": fitz.Rect(bb), "parts": [(bb, t)]}
    if cur:
        rows.append(cur)
    rows.sort(key=lambda r: r["r"].y0)
    return [(r["r"], "".join(p[1] for p in sorted(r["parts"], key=lambda p: p[0].x0))) for r in rows]

pages_by_set, cur = {}, None
for i in range(5, doc.page_count):
    first = ""
    for b in doc[i].get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                first = t; break
        if first:
            break
    m = HDR.match(first)
    if m:
        cur = (m.group(1), m.group(2).strip(), int(m.group(3)))
        pages_by_set.setdefault(cur, []).append(i)
    elif cur is not None:
        pages_by_set[cur].append(i)

os.makedirs(OUT, exist_ok=True)
for _f in os.listdir(OUT):
    if _f.startswith("jiaji_"):
        os.remove(os.path.join(OUT, _f))
order = list(pages_by_set.keys())
bad, total = [], 0
for si, key in enumerate(order):
    base = 3001 + si
    expect, code = 1, "选"
    seq = []          # [{"num","code","clips":[(page, Rect)]}]
    for pi in pages_by_set[key]:
        rows = rows_of(doc[pi])
        starts = []   # (row_idx, num)
        for ri, (rr, txt) in enumerate(rows):
            ms = SEC.match(txt)
            if ms:
                code = CODE[ms.group(1)]
                continue
            mq = QNUM.match(txt)
            if mq and int(mq.group(1)) == expect:
                starts.append((ri, expect, code)); expect += 1
        if not starts:
            # 整页都是上一题的续页
            if seq and rows:
                r = fitz.Rect(rows[0][0]) | fitz.Rect(rows[-1][0])
                seq[-1]["bands"].append((pi, r, rows[0][0].y1, None, rows[0][0].y0, None))
            continue
        # 页首到第一题之间的续行属于上一题
        if starts[0][0] > 0 and seq:
            head = rows[:starts[0][0]]
            r = fitz.Rect(head[0][0]) | fitz.Rect(head[-1][0])
            nxt_top = rows[starts[0][0]][0].y0
            seq[-1]["bands"].append((pi, r, head[-1][0].y1, nxt_top, head[0][0].y0, None))
        for k, (ri, num, qcode) in enumerate(starts):
            nxt = starts[k + 1][0] if k + 1 < len(starts) else len(rows)
            band = rows[ri:nxt]
            if not band:
                continue
            rect = fitz.Rect(band[0][0]) | fitz.Rect(band[-1][0])
            prev_bot = rows[ri - 1][0].y1 if ri > 0 else None
            prev_top = rows[ri - 1][0].y0 if ri > 0 else None
            next_top = rows[nxt][0].y0 if nxt < len(rows) else None
            # 向上回溯：花括号/大分式常被拆成多个单字符行，需连续带回
            floor = None
            j = ri - 1
            while j >= 0 and rows[j][0].y1 > rect.y0 - 30.0:
                j -= 1
            if j + 1 <= ri - 1:
                floor = min(rows[k][0].y0 for k in range(j + 1, ri))
            seq.append({"num": num, "code": qcode, "bands": [(pi, rect, prev_bot, next_top, prev_top, floor)]})
        # 下一页若延续本题，在下一轮循环里通过"整页续页"补上

    if len(seq) != 22:
        bad.append((base, key[1], key[2], len(seq)))
    for q in seq:
        ims = []
        for (pi, rect, prev_bot, next_top, prev_top, floor) in q["bands"]:
            page = doc[pi]
            if floor is not None:
                top = max(30.0, floor - 1.0)
            elif prev_bot is None:
                top = max(30.0, rect.y0 - 12.0)
            else:
                top = max(30.0, (prev_top - 1.0) if (rect.y0 - prev_bot) < 26.0 else (prev_bot + 1.0))
            top = min(top, rect.y0)
            bot = (min(rect.y1 + 6.0, next_top - 4.0)) if next_top is not None else (rect.y1 + 6.0)
            bot = max(bot, rect.y1)
            bot = min(bot, page.rect.height - 8)
            clip = fitz.Rect(40, top, min(578, page.rect.width - 12), bot) & page.rect
            pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2.0, 2.0), colorspace=fitz.csGRAY)
            im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
            ims.append(im.point(lambda p: 0 if p < 215 else 255, mode="1"))
        w = max(i.width for i in ims); h = sum(i.height for i in ims) + 8 * (len(ims) - 1)
        canvas = Image.new("1", (w, h), 1); y = 0
        for i in ims:
            canvas.paste(i, (0, y)); y += i.height + 8
        canvas.save(os.path.join(OUT, f"jiaji_{base:04d}-{REN[q['code']]}{q['num']:02d}.png"), optimize=True)
        total += 1
print("套数", len(order), "图片", total, "异常套", bad[:8])