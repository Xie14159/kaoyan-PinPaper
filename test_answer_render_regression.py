# -*- coding: utf-8 -*-
"""参考答案行渲染回归测试 v2（DS 复盘建议固化）
运行：& ".venv\Scripts\python.exe" test_answer_render_regression.py
- 语义比较前对 HTML 实体做解码归一化（&#x27;→' &amp;→& &gt;→> &lt;→<）
- 03-解-20 断言：LaTeX 命令只出现在 ans-katex（KaTeX 输入）内，不出现在字面 span 内
- HTML 注入断言：不含未转义的原始 <script> 标签
"""
import sys, io, json, re, html
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.pdf_service import PDFService, _fix_dollars
from core.ai_solutions import _clean_final_answer

svc = PDFService()
FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAIL.append(name)


def old_flow(a):
    a2 = _clean_final_answer(a).replace("\n", " ")
    return ("K", a2) if svc._is_math_answer(a2) else ("L", a2)


def semantic(h):
    """提取 (类别, 内容) 序列；内容做实体解码 + 换行归一（语义等价）"""
    seq = []
    for m in re.finditer(
        r'<span class="ans-katex">\$(.*?)\$</span>|<span style="[^"]*">(.*?)</span>', h, re.S
    ):
        body = (m.group(1) if m.group(1) is not None else m.group(2)).replace("\n", " ")
        seq.append(("K" if m.group(1) is not None else "L", html.unescape(body)))
    return seq


def literal_spans(h):
    """提取字面 span（非 ans-katex）的内容"""
    return [html.unescape(m.group(1)) for m in re.finditer(r'<span style="[^"]*">(.*?)</span>', h, re.S)]


print("== 1) 94 条缓存答案全量回归 ==")
cache_p = Path(__file__).resolve().parent / "user_data" / "ai_solutions.json"
c = json.loads(io.open(cache_p, encoding="utf-8").read())
answers = [(qid, (e.get("answer") or "").strip()) for qid, e in c.items() if (e.get("answer") or "").strip()]
check("缓存答案数=94", len(answers) == 94, f"实际 {len(answers)}")

no_dollar = [x for x in answers if "$" not in x[1]]
with_dollar = [x for x in answers if "$" in x[1]]

# 无 $：语义（类别+解码内容）与旧流程完全一致
no_dollar_bad = 0
for _, a in no_dollar:
    o = old_flow(a)
    n = semantic(svc._render_answer_html(a))
    if len(n) != 1 or n[0][0] != o[0] or n[0][1] != o[1]:
        no_dollar_bad += 1
check(f"无$答案语义零回归（{len(no_dollar)}条）", no_dollar_bad == 0, f"{no_dollar_bad} 条差异")

# 含 $：全部出现 KaTeX 段，且字面 span 内无裸 LaTeX 命令残留
with_dollar_bad = 0
for _, a in with_dollar:
    h = svc._render_answer_html(a)
    if not any(k == "K" for k, _ in semantic(h)):
        with_dollar_bad += 1
    for lit in literal_spans(h):
        if re.search(r"\\[a-zA-Z]+", lit):
            with_dollar_bad += 1
            break
check(f"含$答案 KaTeX 渲染且字面无源码残留（{len(with_dollar)}条）", with_dollar_bad == 0, f"{with_dollar_bad} 条异常")

print("== 2) 关键边界用例 ==")
cases = [
    # (名称, 输入, 断言: 必须含KaTeX, 字面span内必须不含)
    ("真实03-解-20", "(I) 证明见推导；(II) $\\displaystyle\\int_{-\\pi/4}^{\\pi/4}\\dfrac{\\mathrm{d}x}{(1+e^x)\\cos^2 x}=1", True, ["dfrac", "mathrm"]),
    ("真实03-解-19", "(I) $g(x)$ 的定义域为 $[-1,3]$，值域为 $[0,2]$；(II) $1", True, []),
    ("无$纯选项", "选项 A", False, []),
    ("无$纯公式", "\\frac{1}{2}", True, []),
    ("HTML注入", "$<script>alert(1)</script>$", True, ["<script>"]),
    ("空公式", "答案：$$", False, []),
    ("DS错配例", "答案 $x$ 和 $y$ 满足 $x+y=1", True, []),
    ("boxed包裹", "\\boxed{\\frac{\\pi}{4}}", True, []),
]
for name, a, want_katex, must_not_in_literal in cases:
    h = svc._render_answer_html(a)
    has_k = any(k == "K" for k, _ in semantic(h))
    check(f"边界[{name}] KaTeX{'有' if want_katex else '无'}", has_k == want_katex)
    for bad in must_not_in_literal:
        check(f"边界[{name}] 字面不含{bad!r}", all(bad not in lit for lit in literal_spans(h)))

print("== 3) _fix_dollars 配对修复 ==")
check("奇数$尾部公式补闭合", _fix_dollars("答案 $x^2+1") == "答案 $x^2+1$")
check("奇数$尾部文本剥除", _fix_dollars("答案 $x$ 和 $y$ 满足 $x+y=1").endswith("x+y=1"))
check("偶数$不动", _fix_dollars("$a$ 和 $b$") == "$a$ 和 $b$")
check("$$归一", _fix_dollars("$$x$$") == "$x$")

print()
if FAIL:
    print(f"共 {len(FAIL)} 项失败: {FAIL}")
    sys.exit(1)
print("全部通过 ✅")
