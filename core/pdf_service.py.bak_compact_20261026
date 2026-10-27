"""
考研数学智能组卷系统 - A4 PDF 排版与导出服务
1:1 像素级复刻前端 Streamlit 原生渲染体验：
- 采用与前端一致的 Python-Markdown + KaTeX 占位符保真渲染
- 采用与前端 st.container(border=True) 一致的轻质圆角卡片容器
- 采用与前端 st.columns(2) 一致的双列选项排版
- 去除一切冗余修饰，仅保留纯净题号、题干与选项
"""
from __future__ import annotations

import html
import logging
import os
import re
import shutil
import subprocess
import io
import tempfile
from enum import Enum
from pathlib import Path, PurePath

import markdown

from core.ai_solutions import AI_MARK, get_ai_solution, stem_fingerprint, split_answer_from_text, _is_placeholder, _is_choice_type, _clean_final_answer
from core.models import PaperItem, QuestionItem, QuestionType, SubjectType

logger = logging.getLogger(__name__)


class PDFEdition(str, Enum):
    REAL_EXAM = "real_exam"      # 真题/模考版：1:1 复刻前端做题卡片
    WORKBOOK_A4 = "workbook_a4"  # A4 做题本版：预留手写草稿与大题演算框
    SOLUTION = "solution"        # 详细解析版：参考答案与分步解析


def _fix_dollars(a: str) -> str:
    """参考答案公式定界符配对修复（AI 输出 $ 常不成对，是参考答案行乱码根因）：
    1. $$...$$ 块级归一为 $...$ 行内（简化配对，避免块级错配）
    2. 奇数个 $：最后一个 $ 之后的尾部含 LaTeX 命令/上下标 → 视为未闭合公式，补一个 $ 闭合；
       否则视为孤立定界符，剥掉它（宁当文本，不显示 $ 源码）
    （DS：先修复配对再整体判定；Claude：分段渲染。两者综合为"配对修复 + 分段"）
    """
    v = (a or "").replace("$$", "$")
    n = v.count("$")
    if n % 2 == 0:
        return v
    last = v.rfind("$")
    tail = v[last + 1:]
    if re.search(r"\\[a-zA-Z]+|[\^_{}]", tail):
        # 尾部是未闭合公式（$ 后有 LaTeX 命令/上下标花括号）→ 补闭合 $
        logger.info("[ans-render] _fix_dollars 补闭合$: %s...", (v[:40] or "").replace("\n", " "))
        return v + "$"
    # 扫描"孤立闭合 $"（丢前导 $ 的公式）：前邻是公式特征，且无前 $ 或与前 $ 之间有中文
    i = v.find("$")
    while i != -1:
        prev_ch = v[i - 1] if i > 0 else ""
        if re.search(r"[}\)\]a-zA-Z0-9]", prev_ch):
            j = v.rfind("$", 0, i)
            if j == -1 or re.search(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", v[j + 1:i]):
                seg_all = v[(j + 1 if j != -1 else 0):i]
                if re.search(r"\\[a-zA-Z]+|[\^_{}]", seg_all):
                    # 内容含 LaTeX 特征 → 真公式丢前导 → 在其起点补 $
                    start = j + 1 if j != -1 else 0
                    m = re.search(r"\\[a-zA-Z]+", seg_all)
                    ins = start + (m.start() if m else 0)
                    logger.info("[ans-render] _fix_dollars 补前导$: %s...", (v[:40] or "").replace("\n", " "))
                    return v[:ins] + "$" + v[ins:]
        i = v.find("$", i + 1)
    logger.info("[ans-render] _fix_dollars 剥孤立$: %s...", (v[:40] or "").replace("\n", " "))
    return v[:last] + v[last + 1:]


class PDFService:
    """A4 高保真试卷排版与导出服务（1:1 复刻前端渲染）"""

    def __init__(self):
        self._assets_dir = Path(__file__).resolve().parent.parent / "assets" / "katex"  # 绝对路径，不依赖 cwd
        self._md = markdown.Markdown(extensions=["tables", "fenced_code"])

    def _get_katex_headers(self) -> str:
        """注入 KaTeX 资源与自动渲染脚本（优先使用本地内联，0ms 网络依赖，100% 离线可用）"""
        js_file = self._assets_dir / "katex.min.js"
        css_file = self._assets_dir / "katex.min.css"
        render_file = self._assets_dir / "auto-render.min.js"

        macro_js = """
            macros: {
                "\\\\wideparen": "\\\\overset{\\\\frown}{#1}",
                "\\\\oiint": "\\\\iint",
                "\\\\mathring": "\\\\overset{\\\\circ}{#1}"
            },
            throwOnError: false
        """

        if js_file.exists() and css_file.exists() and render_file.exists():
            css_content = css_file.read_text(encoding="utf-8")
            # 参考答案栏 KaTeX 渲染失败兜底（throwOnError:false 的 .katex-error 类）：
            # 原样 LaTeX 以可读样式显示（保留字面而非刺眼红色）
            css_content += """
            .katex-error { color: inherit !important; font-family: 'Cambria Math', 'Times New Roman', Consolas, monospace !important;
                           background: #fef3c7; padding: 0 4px; border-radius: 3px; }
            .ans-katex { font-size: 11pt; }
            """
            # 字体引用保持相对路径 url(fonts/xxx.woff2) 原样：
            # 渲染时会把 assets/katex/fonts 复制到临时 HTML 同目录 → 100% 离线，无 CDN 网络依赖
            js_content = js_file.read_text(encoding="utf-8")
            render_content = render_file.read_text(encoding="utf-8")
            return f"""
<style>{css_content}</style>
<script>{js_content}</script>
<script>{render_content}</script>
<script>
    document.addEventListener("DOMContentLoaded", function() {{
        if (typeof renderMathInElement === "function") {{
            renderMathInElement(document.body, {{
                delimiters: [
                    {{left: '$$', right: '$$', display: true}},
                    {{left: '$', right: '$', display: false}},
                    {{left: '\\\\(', right: '\\\\)', display: false}},
                    {{left: '\\\\[', right: '\\\\]', display: true}}
                ],
                {macro_js}
            }});
        }}
    }});
</script>
"""
        return f"""
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<script>
    document.addEventListener("DOMContentLoaded", function() {{
        if (typeof renderMathInElement === "function") {{
            renderMathInElement(document.body, {{
                delimiters: [
                    {{left: '$$', right: '$$', display: true}},
                    {{left: '$', right: '$', display: false}},
                    {{left: '\\(', right: '\\)', display: false}},
                    {{left: '\\[', right: '\\]', display: true}}
                ],
                {macro_js}
            }});
        }}
    }});
</script>
"""

    def _get_body_render_script(self) -> str:
        """body 末尾同步 auto-render（修复 headless Chrome --print-to-pdf 不触发 DOMContentLoaded 监听、
        导致 PDF 里公式显示为裸 LaTeX 的问题）。解析期同步执行：脚本位于 </body> 前，此时 body 已完整，
        无需等待任何事件；渲染后公式即出现在打印快照中。已渲染元素幂等（文本已替换，不会二次处理）。"""
        return """
<script>
(function () {
    if (typeof renderMathInElement !== "function") return;
    try {
        renderMathInElement(document.body, {
            delimiters: [
                {left: '$$', right: '$$', display: true},
                {left: '$', right: '$', display: false},
                {left: '\\(', right: '\\)', display: false},
                {left: '\\[', right: '\\]', display: true}
            ],
            macros: {
                "\\wideparen": "\\overset{\\frown}{#1}",
                "\\oiint": "\\iint",
                "\\mathring": "\\overset{\\circ}{#1}"
            },
            throwOnError: false
        });
    } catch (e) {}
})();
</script>
"""

    def _get_subject_info(self, subject: SubjectType) -> tuple[str, str]:
        """获取科目中文与代码"""
        if subject == SubjectType.MATH_1:
            return "一", "301"
        elif subject == SubjectType.MATH_2:
            return "二", "302"
        elif subject == SubjectType.MATH_3:
            return "三", "303"
        return "一", "301"

    @staticmethod
    def preprocess_math_string(s: str) -> str:
        """解决所有 LaTeX / KaTeX 语法不兼容与特殊字符宏映射"""
        s = re.sub(r'\\wideparen\s*\{([^}]+)\}', r'\\overset{\\frown}{\1}', s)
        s = re.sub(r'\\wideparen\s+([A-Za-z0-9]+)', r'\\overset{\\frown}{\1}', s)
        s = re.sub(r'\\\\([a-zA-Z])', r'\\\\ \1', s)
        s = s.replace('<', r'\lt ').replace('>', r'\gt ')
        s = s.replace(r'\oiint', r'\iint')
        s = re.sub(r'\\mathring\s*\{([^}]+)\}', r'\\overset{\\circ}{\1}', s)
        return s

    @staticmethod
    def format_math_text(text: str) -> str:
        r"""
        1:1 复刻前端 Markdown 与数学公式渲染：
        1. 剥离前缀序号
        2. 占位保护 $$...$$ 与 $...$ 公式，防止 Markdown 转移特殊字符
        3. 用标准 Python-Markdown 解析段落与表格
        4. 还原公式占位符供 KaTeX 渲染
        """
        if not text:
            return ""
        
        # 剥离前缀序号
        s = re.sub(r"^(?:\*\*\(\d+\)\*\*|\(\d+\)|（\d+）|\d+[.．、])\s*", "", text.strip())

        math_store: dict[str, str] = {}
        
        def stash_math(m: re.Match) -> str:
            idx = len(math_store)
            placeholder = f"MATHSTASHXYZ{idx}END"
            raw = m.group(0)
            if raw.startswith('$$'):
                clean_math = f"$${PDFService.preprocess_math_string(raw[2:-2].strip())}$$"
            elif raw.startswith(r'\['):
                # \[...\] 块级公式 → $$...$$（兼容 AI 输出的学术定界符）
                clean_math = f"$${PDFService.preprocess_math_string(raw[2:-2].strip())}$$"
            elif raw.startswith(r'\('):
                # \(...\) 行内公式 → $...$
                clean_math = f"${PDFService.preprocess_math_string(raw[2:-2].strip())}$"
            else:
                clean_math = f"${PDFService.preprocess_math_string(raw[1:-1])}$"
            math_store[placeholder] = clean_math
            return placeholder

        # 1. 占位保护数学公式（$$..$$ / $..$ / \[...\] / \(...\)）
        s_stashed = re.sub(
            r'(\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\))',
            stash_math, s, flags=re.DOTALL)

        # 2. Markdown 标准转换（自然段落 <p> 与表格 <table>）
        md = markdown.Markdown(extensions=["tables", "fenced_code"])
        html_out = md.convert(s_stashed)

        # 3. 还原数学公式
        for placeholder, clean_math in math_store.items():
            html_out = html_out.replace(placeholder, clean_math)

        return html_out

    @staticmethod
    def _render_options_grid(options: list[str]) -> str:
        """1:1 复刻前端 Streamlit st.columns(2) 双列选项排版"""
        if not options:
            return ""
        
        # 估算可见字符长度
        def estimate_visible_len(opt: str) -> int:
            clean = re.sub(r"\\[a-zA-Z]+", "", opt)
            clean = re.sub(r"[\${}\\\s]", "", clean)
            return len(clean)

        max_vis = max((estimate_visible_len(opt) for opt in options), default=0)

        # 超短纯选项（如 A. 0, B. 1, C. 2, D. 3）4 列并排
        if max_vis <= 8 and len(options) == 4:
            grid_style = "grid-template-columns: repeat(4, 1fr);"
        # 超长段落（超 80 个字符的论述型文字）单列排版
        elif max_vis > 80:
            grid_style = "grid-template-columns: 1fr;"
        # 所有常规数学公式与选择题：标准双列（与网页端 1:1 对称）
        else:
            grid_style = "grid-template-columns: 1fr 1fr;"

        opt_items = "".join(f'<div class="opt-item">{PDFService.format_math_text(opt)}</div>' for opt in options)
        return f'<div class="options-grid" style="{grid_style}">{opt_items}</div>'

    @staticmethod
    def format_question_stem(q_idx: int, stem_text: str) -> str:
        """格式化题干：剥离原有冗余序号，将纯黑序号（如 1. ）置于首行题干最前列，实现标准试卷流式排版"""
        formatted = PDFService.format_math_text(stem_text)
        num_prefix = f'<span class="q-num">{q_idx}.&nbsp;</span>'
        if formatted.startswith("<p>"):
            return f"<p>{num_prefix}" + formatted[3:]
        elif formatted.startswith("<div>"):
            return f"<div>{num_prefix}" + formatted[5:]
        else:
            return f"<p>{num_prefix}{formatted}</p>"

    def _get_common_css(self) -> str:
        """标准试卷纯净排版样式（无框线，序号前置）"""
        return """
@page {
    size: A4 portrait;
    margin: 16mm 14mm 16mm 14mm;
    @bottom-center {
        content: "第 " counter(page) " 页 · 共 " counter(pages) " 页";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 8.5pt;
        color: #94a3b8;
    }
}
* {
    box-sizing: border-box;
}
body {
    font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 10.5pt;
    line-height: 1.6;
    color: #0f172a;
    background: #ffffff;
    margin: 0;
    padding: 0;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
}

/* 顶部标题栏 */
.paper-header {
    text-align: center;
    border-bottom: 1.5px solid #000000;
    padding-bottom: 5px;
    margin-bottom: 10px;
}
.paper-title {
    font-size: 15pt;
    font-weight: 800;
    color: #000000;
    margin: 0 0 4px 0;
}
.paper-meta {
    font-size: 9.5pt;
    color: #475569;
}

/* 大题分组标题 */
.section-title {
    font-size: 11pt;
    font-weight: 800;
    color: #000000;
    margin: 10px 0 6px 0;
    padding-bottom: 3px;
    border-bottom: 1px solid #cbd5e1;
    page-break-after: avoid;
}

/* 题目排版（纯净无外框、无背景底色） */
.q-card {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    margin-bottom: 8px;
    page-break-inside: avoid;
}
.q-num {
    font-weight: 800;
    font-size: 10.5pt;
    color: #000000;
    display: inline;
}
.q-stem {
    font-size: 10.5pt;
    line-height: 1.55;
    color: #000000;
}
.q-stem p {
    margin: 0 0 3px 0;
}
.q-stem p:last-child {
    margin-bottom: 0;
}

/* 选项网格 */
.options-grid {
    display: grid;
    gap: 3px 16px;
    margin: 4px 0 2px 0;
    font-size: 10pt;
    color: #000000;
}
.opt-item p {
    margin: 0;
    line-height: 1.6;
}

/* 数据表格 */
table {
    border-collapse: collapse;
    margin: 8px 0;
    font-size: 10pt;
    text-align: center;
    background: #ffffff;
}
table th, table td {
    border: 1px solid #cbd5e1;
    padding: 5px 14px;
    text-align: center;
    font-weight: normal;
    min-width: 40px;
}
table th {
    background: #f8fafc;
    font-weight: 600;
}

/* 做题本纯手写留白区（无外框、无虚线、无提示文字，纯留白空间） */
.workspace-box {
    border: none;
    background: transparent;
    padding: 0;
    margin: 0;
}
.wb-choice-box {
    height: 40mm;
}
.wb-fill-box {
    height: 60mm;
}
.wb-solution-box {
    height: 140mm;
}

/* 题目元信息行(书籍/难度/章节/ID/考点标签) —— 与 app 卡片一致 */
.meta-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    margin: 1px 0 3px 0;
}
.badge {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 5px;
    font-size: 8.5pt;
    font-weight: 600;
    line-height: 1.5;
    white-space: nowrap;
}
.badge-basic { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
.badge-comp  { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
.badge-adv   { background: #faf5ff; color: #7e22ce; border: 1px solid #e9d5ff; }
.badge-ch    { background: #f8fafc; color: #475569; border: 1px solid #e2e8f0; }
.badge-tag   { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.meta-id {
    font-size: 8.5pt;
    color: #64748b;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}

/* 解析版区块 */
.solution-block {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 3px solid #2563eb;
    border-radius: 4px;
    padding: 6px 10px;
    margin-top: 6px;
    font-size: 9.5pt;
    color: #0f172a;
}
.solution-block p {
    margin: 2px 0;
}

/* KaTeX 与正文基线对齐 */
.katex {
    font-size: 1.04em !important;
    text-rendering: optimizeLegibility;
}
.katex-display {
    margin: 0.25em 0 !important;
}
"""

    # =========================================================================
    # 1. 模考/真题版 HTML
    # =========================================================================
    def _build_real_exam_html(self, paper: PaperItem) -> str:
        sub_cn, _ = self._get_subject_info(paper.subject)
        
        choices = [q for q in paper.questions if q.question_type == QuestionType.CHOICE]
        fills = [q for q in paper.questions if q.question_type == QuestionType.FILL_BLANK]
        solutions = [q for q in paper.questions if q.question_type == QuestionType.SOLUTION]

        blocks: list[str] = []
        q_idx = 1

        if choices:
            blocks.append(f'<div class="section-title">一、选择题（共 {len(choices)} 题，每题 5 分，共 {len(choices)*5} 分）</div>')
            for q in choices:
                stem_html = self.format_question_stem(q_idx, q.stem)
                options_html = self._render_options_grid(q.options)
                blocks.append(f"""
                <div class="q-card">
                    <div class="q-stem">{stem_html}</div>
                    {options_html}
                </div>
                """)
                q_idx += 1

        if fills:
            blocks.append(f'<div class="section-title">二、填空题（共 {len(fills)} 题，每题 5 分，共 {len(fills)*5} 分）</div>')
            for q in fills:
                stem_html = self.format_question_stem(q_idx, q.stem)
                blocks.append(f"""
                <div class="q-card">
                    <div class="q-stem">{stem_html}</div>
                </div>
                """)
                q_idx += 1

        if solutions:
            blocks.append(f'<div class="section-title">三、解答题（共 {len(solutions)} 题，共 70 分）</div>')
            for q in solutions:
                stem_html = self.format_question_stem(q_idx, q.stem)
                blocks.append(f"""
                <div class="q-card">
                    <div class="q-stem">{stem_html}</div>
                </div>
                """)
                q_idx += 1

        body_content = "\n".join(blocks)
        katex_head = self._get_katex_headers()
        common_css = self._get_common_css()

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>2026 年考研数学{sub_cn}全真模拟试卷</title>
{katex_head}
<style>
{common_css}
</style>
</head>
<body>

<div class="paper-header">
    <div class="paper-title">2026 年全国硕士研究生招生考试数学（{sub_cn}）模拟试卷</div>
    <div class="paper-meta">考试时间: 180 分钟 · 满分: 150 分 · 共 {paper.total_count} 题 · 卷号: {paper.paper_id}</div>
</div>

{body_content}

</body>
</html>"""

    # =========================================================================
    # 2. A4 做题本版 HTML (纯留白无框线无注释)
    # =========================================================================
    def _build_workbook_html(self, paper: PaperItem) -> str:
        sub_cn, _ = self._get_subject_info(paper.subject)
        
        choices = [q for q in paper.questions if q.question_type == QuestionType.CHOICE]
        fills = [q for q in paper.questions if q.question_type == QuestionType.FILL_BLANK]
        solutions = [q for q in paper.questions if q.question_type == QuestionType.SOLUTION]

        blocks: list[str] = []
        q_idx = 1

        if choices:
            blocks.append(f'<div class="section-title">一、选择题（共 {len(choices)} 题）</div>')
            for q in choices:
                stem_html = self.format_question_stem(q_idx, q.stem)
                options_html = self._render_options_grid(q.options)
                workspace = '<div class="workspace-box wb-choice-box"></div>'
                blocks.append(f"""
                <div class="q-card">
                    <div class="q-stem">{stem_html}</div>
                    {options_html}
                    {workspace}
                </div>
                """)
                q_idx += 1

        if fills:
            blocks.append(f'<div class="section-title">二、填空题（共 {len(fills)} 题）</div>')
            for q in fills:
                stem_html = self.format_question_stem(q_idx, q.stem)
                workspace = '<div class="workspace-box wb-fill-box"></div>'
                blocks.append(f"""
                <div class="q-card">
                    <div class="q-stem">{stem_html}</div>
                    {workspace}
                </div>
                """)
                q_idx += 1

        if solutions:
            blocks.append(f'<div class="section-title">三、解答题（共 {len(solutions)} 题）</div>')
            for q in solutions:
                stem_html = self.format_question_stem(q_idx, q.stem)
                workspace = '<div class="workspace-box wb-solution-box"></div>'
                blocks.append(f"""
                <div class="q-card">
                    <div class="q-stem">{stem_html}</div>
                    {workspace}
                </div>
                """)
                q_idx += 1

        body_content = "\n".join(blocks)
        katex_head = self._get_katex_headers()
        common_css = self._get_common_css()

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>2026 年考研数学{sub_cn} A4 留白做题本</title>
{katex_head}
<style>
{common_css}
</style>
</head>
<body>

<div class="paper-header">
    <div class="paper-title">考研数学《880》A4 留白做题本（数学{sub_cn}）</div>
    <div class="paper-meta">全卷留白演算空间 · 适合 iPad 导题与 A4 打印刷题 · 卷号: {paper.paper_id}</div>
</div>

{body_content}

</body>
</html>"""

    # =========================================================================
    # 3. 详细解析版 HTML
    # =========================================================================
    @staticmethod
    def _build_meta_row(q: QuestionItem) -> str:
        """题目元信息行:与 app「查看答案与解析」展开后的一致(书籍/难度/章节/ID/考点标签)。"""
        diff_val = q.difficulty.value
        diff_cls = "badge-basic" if diff_val == "基础题" else ("badge-adv" if diff_val == "拓展题" else "badge-comp")
        book = getattr(q, "book", "880") or "880"
        tags_html = "".join(
            f'<span class="badge badge-tag">#{html.escape(str(t))}</span>' for t in q.tags
        ) if q.tags else ""
        return (
            '<div class="meta-row">'
            f'<span class="badge badge-ch">《{html.escape(str(book))}》</span>'
            f'<span class="badge {diff_cls}">[{html.escape(diff_val)}]</span>'
            f'<span class="badge badge-ch">{html.escape(str(q.chapter))}</span>'
            f'<span class="meta-id">ID: {html.escape(str(q.id))}</span>'
            f'{tags_html}'
            '</div>'
        )

    @staticmethod
    def _is_math_answer(ans: str) -> bool:
        """参考答案栏公式判定（DS+Claude 评审共识：白名单字面 → LaTeX 特征 → 危险字符排除）：
        选项字母/多选/纯中文/纯数字 → 字面；含反斜杠命令、上下标、分组、Unicode 数学符 → KaTeX 渲染；
        含 $ % # & ~ < > ` 等危险字符 → 强制字面（防破坏 auto-render 定界符 / KaTeX 注释 / HTML 注入）。"""
        if not ans or not ans.strip():
            return False
        a = ans.strip().replace("\n", " ")
        if not a:
            return False
        # 危险字符 → 字面（$ 定界符 / % LaTeX 注释 / # 宏参数 / < > 破坏 HTML / ` 反引号）
        if any(ch in a for ch in ("$", "%", "#", "<", ">", "`")):
            return False
        # & 在 aligned/矩阵环境内是合法对齐符，仅当无 \begin 上下文时视为危险（DS 终审共识）
        if "&" in a and "\\begin" not in a:
            return False
        # 纯选项字母（单选 B / 多选 A、B / AB）
        if re.fullmatch(r"[A-Da-d](?:[\u3001,，;；和及与 ]\s*[A-Da-d])*|[A-Da-d]{1,4}", a):
            return False
        # 纯中文/短文本（无 LaTeX 特征，如"无解""不存在""略"）
        if re.fullmatch(r"[\u4e00-\u9fff，。、（）()0-9a-zA-Z\s]*", a) and not re.search(r"[\\^{}_]", a):
            return False
        # LaTeX 特征：\ 命令 / 上下标 / 分组
        if "\\" in a or "^" in a or "_" in a or "{" in a or "}" in a:
            return True
        # Unicode 数学符号
        if re.search(r"[π√≤≥≠∞∈±×÷∫∑∏θαβγδλμσφω]", a):
            return True
        # 兜底：含等号 / 数字斜杠分数 / 数字字母混合表达式
        if "=" in a or re.search(r"[0-9]+\s*/\s*[0-9]+", a):
            return True
        if re.search(r"[0-9]", a) and re.search(r"[a-zA-Z]", a):
            # 排除题号/选项+题号残留形态（A2、B12）→ 字面（DS 终审共识：数字字母混合需附加特征才判公式）
            if re.fullmatch(r"[A-Za-z]\d+", a):
                return False
            return True
        return False


    @staticmethod
    def _lit_span(txt: str) -> str:
        """参考答案行字面显示（灰底高亮，html.escape 防注入）"""
        return (f'<span style="background:#f1f5f9; padding:1px 6px; border-radius:4px; font-weight:bold;">'
                f'{html.escape(txt)}</span>')

    def _render_answer_html(self, ans_str: str) -> str:
        """参考答案行渲染（乱码修复定稿）：
        - 无 $：维持原判定（公式 → KaTeX / 其他 → 字面）
        - 有 $：_fix_dollars 修复配对 → 按 $...$ 切分，公式段 KaTeX、文本段字面（剥孤立 $ 与定界符残留）
        - 所有输出 html.escape；KaTeX 渲染失败走 .katex-error 兜底（显示源码不报错）
        """
        a = (ans_str or "").strip()
        if not a:
            return ""
        if "$" not in a:
            # 无 $：维持原判定（公式 → KaTeX / 其他 → 字面）；先 _clean_final_answer 保留原清理行为（防回归）
            a2 = _clean_final_answer(a)
            if self._is_math_answer(a2):
                return f'<span class="ans-katex">${html.escape(a2.replace(chr(10), " "))}$</span>'
            return self._lit_span(a2 or a)
        a = _fix_dollars(a)
        if "$" not in a:
            a2 = _clean_final_answer(a)
            if self._is_math_answer(a2):
                return f'<span class="ans-katex">${html.escape(a2.replace(chr(10), " "))}$</span>'
            return self._lit_span(a2 or a)
        out: list[str] = []
        for seg in re.split(r"(\$[^$]+\$)", a):
            if not seg:
                continue
            if seg.startswith("$") and seg.endswith("$") and len(seg) > 2:
                inner = seg[1:-1].strip()
                if inner and not re.search(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", inner):
                    # 公式段：无中文 → KaTeX（.katex-error 兜底显示源码，不报错）
                    out.append(f'<span class="ans-katex">${html.escape(inner.replace(chr(10), " "))}$</span>')
                elif inner:
                    # 含中文的"公式段"（如"（即 "）→ 字面，防错配渲染
                    logger.info("[ans-render] 公式段含中文→字面兜底: %s...", (inner[:40] or "").replace("\n", " "))
                    out.append(self._lit_span(_clean_final_answer(inner) or inner))
            else:
                txt = _clean_final_answer(seg.replace("$", "")).strip()
                if txt and not re.search(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", txt) and self._is_math_answer(txt):
                    # 文本段若为纯公式源码（开头丢$的残缺定界符答案）→ KaTeX 渲染
                    out.append(f'<span class="ans-katex">${html.escape(txt.replace(chr(10), " "))}$</span>')
                elif txt:
                    out.append(self._lit_span(txt))
        return "".join(out)

    def _build_solution_html(self, paper: PaperItem) -> str:
        sub_cn, _ = self._get_subject_info(paper.subject)

        blocks: list[str] = []
        for idx, q in enumerate(paper.questions, start=1):
            stem_html = self.format_question_stem(idx, q.stem)
            meta_row = self._build_meta_row(q)
            options_html = self._render_options_grid(q.options) if q.options else ""

            # 优先使用 AI 名师补全的答案/解析（本地缓存），其次用题库自带
            ai_sol = get_ai_solution(q.id, stem_fp=stem_fingerprint(q))
            if ai_sol:
                # M6：题库自带有效答案优先，AI 结果作补充（防 AI 提取错误覆盖官方答案）；
                # 题库 answer 为空/占位符（"略"）不算有效答案 → 回退 AI 提取的答案；
                # AI 缓存 answer 为空（定稿解析无【答案】标记）→ 现场从解析提取自然语言结论（覆盖存量缓存）
                # 现场提取优先（含【最终答案】定稿分支）：存量缓存 answer 可能提取自第一个【标准答案】
                # （错误中间答案），现场提取会覆盖为正文定稿答案；提取失败再回退缓存 answer
                # 契约锁定(勿删): 约27条历史缓存 answer 字段为空(题库答案即"略"), 依赖此处从 solution
                # 现场提取答案 —— 改动此行必须保证 solution 提取仍可用, 否则这27题参考答案会退化。
                _ai_answer = split_answer_from_text(ai_sol.get("solution") or "", is_choice=_is_choice_type(q)) or ai_sol.get("answer") or ""
                if q.answer and not _is_placeholder(q.answer):
                    ans_str = q.answer
                elif _ai_answer:
                    ans_str = _ai_answer
                else:
                    ans_str = "见详细解析"
                sol_text = q.solution or ai_sol.get("solution") or "详见标准解析推导。"
                _rs = ai_sol.get("review_status")
                if _rs == "verified":
                    _mark_txt = "（AI 名师生成 · 已审核）"
                elif _rs == "fixed":
                    _mark_txt = "（AI 名师生成 · 已审核修正）"
                elif _rs in ("pdf", "pdf_fixed"):
                    _mark_txt = "（扫描版解析核验）"
                elif _rs == "doubt":
                    _mark_txt = "（AI 名师生成 · 待核实）"
                elif _rs == "failed":
                    _mark_txt = "（解析生成失败）"
                elif _rs == "human":
                    _mark_txt = "（AI 名师生成 · 人工核对）"
                elif _rs == "unchecked":
                    _mark_txt = "（AI 名师生成 · 未审核）"
                elif _rs == "review_failed":
                    _mark_txt = "（AI 名师生成 · 审核失败）"
                else:
                    _mark_txt = AI_MARK
                sol_source_mark = f'<span style="color:#64748b; font-size:8.5pt; font-weight:600;">{_mark_txt}</span>'
            else:
                ans_str = q.answer if q.answer else "略"
                sol_text = q.solution if q.solution else "详见标准解析推导。"
                sol_source_mark = ""
            sol_html = self.format_math_text(sol_text)

            # 参考答案栏渲染（乱码修复）：无 $ 按原判定；有 $ 修复配对后分段渲染（公式 KaTeX / 文本字面）
            _ans_html = self._render_answer_html(ans_str)
            solution_box = f"""
            <div class="solution-block">
                <div style="margin-bottom:4px;"><b>【参考答案】</b>：{_ans_html}{sol_source_mark}</div>
                <div><b>【详细推导与解析步骤】</b>：<div style="margin-top:4px; color:#1e293b;">{sol_html}</div></div>
            </div>
            """

            blocks.append(f"""
            <div class="q-card">
                <div class="q-stem">{stem_html}</div>
                {meta_row}
                {options_html}
                {solution_box}
            </div>
            """)

        body_content = "\n".join(blocks)
        katex_head = self._get_katex_headers()
        common_css = self._get_common_css()
        solution_css = """
/* ===== 解析版专用：压缩版面、允许断页续排（题干整体不拆） ===== */
.q-card {
    page-break-inside: auto;
}
.q-stem {
    page-break-inside: avoid;
}
.meta-row {
    page-break-inside: avoid;
}
.options-grid {
    page-break-inside: avoid;
}
.solution-block {
    page-break-inside: auto;
}
"""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>2026 年考研数学{sub_cn} 详细解析版</title>
{katex_head}
<style>
{common_css}
{solution_css}
</style>
</head>
<body>

<div class="paper-header">
    <div class="paper-title">考研数学《880》参考答案与详细解析（数学{sub_cn}）</div>
    <div class="paper-meta">全套题目参考答案与分步推导 · 卷号: {paper.paper_id}</div>
</div>

{body_content}

</body>
</html>"""

    def generate_html(self, paper: PaperItem, edition: PDFEdition = PDFEdition.REAL_EXAM) -> str:
        """生成纯净 HTML 页面"""
        if edition == PDFEdition.REAL_EXAM:
            return self._build_real_exam_html(paper)
        elif edition == PDFEdition.WORKBOOK_A4:
            return self._build_workbook_html(paper)
        else:
            return self._build_solution_html(paper)

    def render_pdf_bytes(self, paper: PaperItem, edition: PDFEdition = PDFEdition.REAL_EXAM) -> bytes:
        """输出标准 A4 PDF 二进制流。

        优先用 Headless 浏览器（Edge/Chrome/Chromium，Win 本地或云端 Linux 均支持）——
        浏览器会执行 KaTeX 的 JS，公式正确渲染。浏览器不可用时退回 WeasyPrint（依赖已在
        packages.txt），至少产出可打开的 PDF。两者都不可用才退回 HTML 字节。
        """
        html_content = self.generate_html(paper, edition)
        # 修复 headless --print-to-pdf 不执行 DOMContentLoaded 监听的问题：
        # </body> 前注入同步渲染脚本，保证打印快照中公式已渲染
        if "</body>" in html_content:
            html_content = html_content.replace("</body>", self._get_body_render_script() + "</body>", 1)

        pdf = self._render_via_browser(html_content)
        if pdf:
            return pdf

        pdf = self._render_via_weasyprint(html_content)
        if pdf:
            return pdf

        raise RuntimeError(
            "PDF 渲染失败：本地浏览器（Edge/Chrome）渲染与 WeasyPrint 兜底均不可用。"
            "请确认系统已安装 Microsoft Edge 或 Google Chrome 后重试。"
        )

    @staticmethod
    def _find_browser() -> str | None:
        """跨平台定位 Headless 浏览器：Windows 的 Edge/Chrome，Linux（云端）的 Chromium。"""
        candidates = [
            shutil.which("msedge"),
            shutil.which("chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ]
        return next((b for b in candidates if b and os.path.exists(b)), None)

    def _render_via_browser(self, html_content: str) -> bytes | None:
        """两段式渲染：① --dump-dom + --virtual-time-budget 等待 JS 把公式渲染成 KaTeX span；
        ② 对已渲染 DOM 执行 --print-to-pdf。

        headless=new 的 --print-to-pdf 在文档解析完成时立即打印，不等 virtual time、
        不执行 DOMContentLoaded 里的 auto-render，导致 PDF 中公式显示为裸 LaTeX
        （f'(x) 撇号等几乎不可读）。先 dump-dom 再打印可保证打印快照里公式已渲染。"""
        browser_exe = self._find_browser()
        if not browser_exe:
            return None

        tmp_dir = tempfile.mkdtemp(prefix="kaoyan_pdf_")
        try:
            temp_html = os.path.join(tmp_dir, "paper.html")
            rendered_html = os.path.join(tmp_dir, "rendered.html")
            temp_pdf = os.path.join(tmp_dir, "paper.pdf")
            with io.open(temp_html, "w", encoding="utf-8") as f:
                f.write(html_content)

            # 复制 KaTeX 本地字体到临时目录，使 CSS 的 url(fonts/...) 相对路径离线可用
            try:
                fonts_src = self._assets_dir / "fonts"
                fonts_dst = os.path.join(tmp_dir, "fonts")
                if fonts_src.is_dir() and not os.path.exists(fonts_dst):
                    shutil.copytree(fonts_src, fonts_dst)
            except Exception as ex:
                logger.warning(f"复制 KaTeX 字体到临时目录失败（将退化为浏览器字体缓存）: {ex}")

            # --disable-dev-shm-usage：容器内 /dev/shm 很小，不加 chromium 会崩；云端冷启动慢，超时放宽
            common = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-extensions", "--no-pdf-header-footer"]
            dom_url = Path(temp_html).as_uri()

            # 段①：dump-dom 等待 JS 渲染（virtual-time-budget 推进虚拟时间直至 KaTeX 完成）
            # 源 HTML 是否含公式定界符：用于判定"渲染是否真的发生"（无公式的纯文本卷不要求 katex span）
            has_math = ("$" in html_content or "\\(" in html_content or "\\[" in html_content)
            rendered = None
            for hflag in ("--headless=new", "--headless"):
                cmd = [browser_exe, hflag, *common, "--virtual-time-budget=30000",
                       "--dump-dom", dom_url]
                try:
                    proc = subprocess.run(cmd, capture_output=True, timeout=300)
                    out = proc.stdout.decode("utf-8", errors="replace")
                    # 剔除可能混入 stdout 的浏览器日志：从 HTML 文档起始处截取
                    for marker in ("<!DOCTYPE", "<html", "<head"):
                        idx = out.find(marker)
                        if idx >= 0:
                            out = out[idx:]
                            break
                    # 校验：必须是完整文档（以 </html> 结尾），且渲染确实发生
                    # （源含公式 → dump 必须出现 KaTeX span，否则视为段①失败，避免把半截/未渲染 DOM 当成功）
                    ok_doc = out.strip().endswith("</html>") or "</html>" in out
                    ok_render = (not has_math) or ('class="katex"' in out)
                    if ok_doc and ok_render and len(out) > 1000:
                        rendered = out
                        break
                except Exception as ex:
                    logger.warning(f"Headless dump-dom 尝试失败，尝试备用参数: {ex}")
                    continue

            # 段②：打印已渲染 DOM（公式已是 KaTeX span，无需再等 JS）
            target_html = rendered_html if rendered else temp_html
            if rendered:
                with io.open(target_html, "w", encoding="utf-8") as f:
                    f.write(rendered)
            else:
                # 段①失败：显式标记降级，避免静默产出未渲染公式的 PDF 冒充成功
                logger.warning(
                    "KaTeX 渲染段（dump-dom）失败，将打印原始 HTML；PDF 中公式可能显示为裸 LaTeX。"
                    "已重试 headless 新旧模式，请检查浏览器/虚拟时间参数。"
                )
            for cmd in ([browser_exe, "--headless=new", *common, f"--print-to-pdf={temp_pdf}", target_html],
                        [browser_exe, "--headless", *common, f"--print-to-pdf={temp_pdf}", target_html]):
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=300)
                    if os.path.exists(temp_pdf) and os.path.getsize(temp_pdf) > 0:
                        return open(temp_pdf, "rb").read()
                except Exception as ex:
                    logger.warning(f"Headless PDF 尝试失败，尝试备用参数: {ex}")
                    continue
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
        return None

    def _render_via_weasyprint(self, html_content: str) -> bytes | None:
        """WeasyPrint 兜底：不执行 JS，公式可能显示为原始 LaTeX，但能产出可打开的 PDF。"""
        try:
            from weasyprint import HTML  # 延迟导入：本地无 GTK 时不影响浏览器路径
            return HTML(string=html_content).write_pdf()
        except Exception as ex:
            logger.warning(f"WeasyPrint 兜底渲染失败: {ex}")
            return None
