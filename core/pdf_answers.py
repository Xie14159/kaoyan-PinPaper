"""
考研数学智能组卷系统 - 扫描版解析 PDF 视觉查询服务
基于 DeepSeek V4.1-Flash 原生多模态视觉能力：
AI 名师直接解题失败时，从扫描版解析 PDF 中定位并提取该题的标准答案与解析。
题库题号与新版书不一致时，按【题干内容】匹配（老题库友好）。

使用说明：把解析 PDF（文字版或扫描版均可）放到
  user_data/answer_pdfs/ 目录，或 D:\\考研备考\\考研数学\\pinhaojuan system 目录。
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

VISION_MODEL = "deepseek-flash"
VISION_ENDPOINT = "https://api.deepseek.com/chat/completions"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PDF_DIRS = [
    _PROJECT_ROOT / "user_data" / "answer_pdfs",
    Path(r"D:\考研备考\考研数学\pinhaojuan system"),
]
_IDX_FILE = _PROJECT_ROOT / "user_data" / "pdf_index.json"
_LOOKUP_CACHE_FILE = _PROJECT_ROOT / "user_data" / "pdf_lookup_cache.json"

_doc = None
_doc_path: Path | None = None


# ---------------------------------------------------------------------------
# 查询结果缓存（题干指纹 -> 结果；同题不重复烧视觉调用）
# ---------------------------------------------------------------------------
def _lookup_key(q) -> str:
    import hashlib
    stem = re.sub(r"\s+", "", q.stem or "")
    for a, b in (("（", "("), ("）", ")"), ("，", ","), ("。", "."), ("；", ";"), ("：", ":")):
        stem = stem.replace(a, b)
    # 前40字 + 长度 + 尾20字 用于快速区分；再拼全文 sha256 前 16 位彻底防同章节同首尾碰撞
    raw = f"{q.chapter}|{stem[:40]}|{len(stem)}|{stem[-20:]}|{hashlib.sha256((q.stem or '').encode('utf-8')).hexdigest()[:16]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


_LOOKUP_SCHEMA = 2  # 缓存结构版本；指纹算法/结构变更时 +1，旧缓存整体失效


def _pdf_fingerprint() -> str:
    """PDF 文件指纹（mtime + size）；官方册改版后指纹变化 → 缓存整体失效防污染。"""
    try:
        p = _pdf_file()
        if not p or not p.exists():
            return ""
        st = p.stat()
        return f"{p.name}|{st.st_mtime_ns}|{st.st_size}"
    except Exception:
        return ""


def _load_lookup_cache() -> dict:
    try:
        if _LOOKUP_CACHE_FILE.exists():
            data = json.loads(_LOOKUP_CACHE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            if data.get("schema") != _LOOKUP_SCHEMA:
                return {}
            if data.get("pdf_fp") != _pdf_fingerprint():
                return {}
            return data.get("items") if isinstance(data.get("items"), dict) else {}
    except Exception:
        pass
    return {}


def _save_lookup_cache(items: dict) -> None:
    try:
        _LOOKUP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": _LOOKUP_SCHEMA, "pdf_fp": _pdf_fingerprint(), "items": items}
        tmp = _LOOKUP_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_LOOKUP_CACHE_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 基础：PDF 文件定位 / 渲染 / DeepSeek 视觉调用
# ---------------------------------------------------------------------------
def _pdf_file() -> Path | None:
    """定位解析 PDF：优先 user_data/answer_pdfs，其次项目上级目录。"""
    for d in _PDF_DIRS:
        if d.is_dir():
            files = sorted(p for p in d.glob("*.pdf") if p.is_file())
            if not files:
                continue
            for f in files:
                if "解析" in f.name or "答案" in f.name:
                    return f
            return files[0]
        if d.is_file() and d.suffix.lower() == ".pdf":
            return d
    return None


def _open_doc(path: Path):
    global _doc, _doc_path
    if _doc is None or _doc_path != path:
        import fitz  # PyMuPDF
        _doc = fitz.open(path)
        _doc_path = path
    return _doc


def _api_key() -> str:
    """视觉查询使用用户的 DeepSeek Key（与 AI 配置持久化文件一致）。"""
    try:
        cfg = json.loads((_PROJECT_ROOT / "user_data" / "ai_config.json").read_text(encoding="utf-8"))
        if cfg.get("api_key"):
            return cfg["api_key"]
    except Exception:
        pass
    return os.getenv("DEEPSEEK_API_KEY", "")


def _vision_ask(images_b64: list[str], text: str, timeout: int = 120) -> str:
    """调用 DeepSeek V4.1-Flash 多模态接口；失败返回 ""。"""
    key = _api_key()
    if not key:
        return ""
    content: list[dict] = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        for b64 in images_b64
    ]
    content.append({"type": "text", "text": text})
    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }
    req = urllib.request.Request(
        VISION_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def _render_b64(page_no: int, dpi: int = 120) -> str:
    """把 PDF 第 page_no 页渲染为 JPEG 并 base64 编码（0-based）。"""
    path = _pdf_file()
    if not path:
        return ""
    doc = _open_doc(path)
    pix = doc[page_no].get_pixmap(dpi=dpi)
    return base64.b64encode(pix.tobytes("jpeg")).decode("ascii")


# ---------------------------------------------------------------------------
# 章节 → PDF 页 索引（懒构建 + 本地缓存）
# ---------------------------------------------------------------------------
def _chapter_num(ch: str) -> int:
    m = re.search(r"[0-9一二三四五六七八九十百]+", ch or "")
    if not m:
        return 0
    s = m.group(0)
    if s.isdigit():
        return int(s)
    cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return cn.get(s, 0)


def get_chapter_index(force: bool = False) -> dict:
    """构建并缓存 章节 -> PDF 页(0-based) 索引。
    优先用 PDF 自带书签（准确、零 API），无书签才视觉扫目录。
    返回 {"pdf", "chapters", "sections", "total_pages", "source"}；失败返回 {}。
    """
    path = _pdf_file()
    if not path:
        return {}
    if not force and _IDX_FILE.exists():
        try:
            data = json.loads(_IDX_FILE.read_text(encoding="utf-8"))
            if data.get("pdf") == path.name and data.get("chapters"):
                return data
        except Exception:
            pass

    doc = _open_doc(path)
    total = doc.page_count

    # ---- 书签优先（PyMuPDF get_toc：页码为 1-based 页面序号 → 0-based 页 = 页码 - 1）----
    chapters: dict[str, int] = {}
    sections: dict[str, dict[str, int]] = {}
    _cur: str | None = None
    try:
        for _level, _title, _page in doc.get_toc():
            _t = (_title or "").strip()
            if _level == 1:
                _cur = _t
                chapters[_cur] = max(0, _page - 1)
                sections.setdefault(_cur, {})
            elif _level == 2 and _cur and _t:
                sections[_cur][_t] = max(0, _page - 1)
        if chapters:
            data = {
                "pdf": path.name,
                "chapters": chapters,
                "sections": sections,
                "total_pages": total,
                "source": "bookmark",
            }
            try:
                _IDX_FILE.parent.mkdir(parents=True, exist_ok=True)
                _IDX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
            return data
    except Exception:
        pass
    printed_pages: list[tuple[int, int]] = []  # (pdf页号0-based, 印刷页码)

    # 扫前 8 页：找目录 + 收集印刷页码用于偏移校正（视觉兜底，仅书签缺失时）
    for pno in range(min(8, total)):
        try:
            b64 = _render_b64(pno)
            text = _vision_ask(
                [b64],
                "这是考研数学书《880题解析分册》的扫描页。请回答：\n"
                "1) 本页页脚/页角的印刷页码数字（纯数字，没有就回复'无'）；\n"
                "2) 本页是否为目录页？若是，请逐行列出目录条目，格式严格为：章节名=页码（例如：第十二章 二次型=325）。"
                "只列章节级别的条目（第X章），不要列小节；若不是目录页，回复'非目录'。",
            )
        except Exception:
            text = ""
        if not text:
            continue
        # 印刷页码
        pm = (re.search(r"印刷页码[：: ]*(\d+)", text)
              or re.search(r"页[脚角]页码[：: ]*(\d+)", text))
        if not pm:
            pm = re.search(r"页码[：: ]*(\d+)", text)
        if pm:
            printed_pages.append((pno, int(pm.group(1))))
        # 目录章节
        for line in text.split("\n"):
            line = line.strip()
            mm = re.match(r"^([第][0-9一二三四五六七八九十百]+章[^=]*?)=(\d{1,3})$", line)
            if mm:
                chapters[mm.group(1).strip()] = int(mm.group(2))

    # 偏移 = 印刷页码 - (PDF页号+1)；取众数
    offset = 0
    if printed_pages:
        offsets = [printed - (pno + 1) for pno, printed in printed_pages]
        offset = max(set(offsets), key=offsets.count)

    pdf_chapters = {}
    for name, printed in chapters.items():
        pdf_chapters[name] = max(0, min(printed - offset - 1, total - 1))

    data = {"pdf": path.name, "chapters": pdf_chapters, "sections": {}, "total_pages": total, "source": "vision"}
    try:
        _IDX_FILE.parent.mkdir(parents=True, exist_ok=True)
        _IDX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return data


def _chapter_match(q, chapters: dict) -> str | None:
    """在书签章节中匹配题目的章节：先按章号数字精确匹配，再按主题关键词模糊匹配。
    老题库章号可能对不上新版（如老"第十三章 线性方程组"= 新版"第十章 线性方程组"），
    主题词匹配解决跨版本章节错位。"""
    ch = q.chapter or ""
    num = _chapter_num(ch)
    if num:
        for name in chapters:
            if _chapter_num(name) == num:
                return name
    topic = re.sub(r"^第[0-9一二三四五六七八九十百]+章\s*", "", ch).strip()
    if topic:
        cands = [name for name in chapters if topic and topic in name]
        if len(cands) == 1:
            return cands[0]
    return None


def _section_name(q) -> str:
    """从题目 ID / pian 推断小节名（基础题/综合题/拓展题），用于书签小节定位。"""
    for src in (q.pian or "", q.id or ""):
        if "基础" in src:
            return "基础题"
        if "综合" in src:
            return "综合题"
        if "拓展" in src:
            return "拓展题"
    return ""


def _chapter_page_range(q, index: dict) -> tuple[int, int] | None:
    """返回题目所在区段的 PDF 页范围 [start, end]（0-based）。

    优先按"章节 + 小节（基础题/综合题/拓展题）"定位起点（书签有完整小节）；
    无小节信息则用章节起始页。起点不往前移（书签页码已验证准确），
    由调用方 max_pages 控制扫描页数上限。定位失败返回 None。
    """
    chapters = index.get("chapters", {})
    if not chapters:
        return None
    name = _chapter_match(q, chapters)
    if name is None:
        return None
    total = index.get("total_pages", 0)

    sections = index.get("sections", {})
    sec = _section_name(q)
    if sec and name in sections and sec in sections[name]:
        start = sections[name][sec]
    else:
        start = chapters[name]

    # 结束：下一小节起点 - 1；无小节则下一章起点 - 1；最后一章到全书末尾
    ordered = sorted(chapters.items(), key=lambda kv: kv[1])
    end = total - 1
    if name in sections and sec:
        sec_keys = sorted(sections[name].items(), key=lambda kv: kv[1])
        for i, (sn, sp) in enumerate(sec_keys):
            if sn == sec:
                if i + 1 < len(sec_keys):
                    end = sec_keys[i + 1][1] - 1
                else:
                    for j, (nm, pg) in enumerate(ordered):
                        if nm == name and j + 1 < len(ordered):
                            end = ordered[j + 1][1] - 1
                            break
                break
    else:
        for i, (nm, pg) in enumerate(ordered):
            if nm == name:
                if i + 1 < len(ordered):
                    end = ordered[i + 1][1] - 1
                break
    return max(0, start), min(end, total - 1)


# ---------------------------------------------------------------------------
# 核心：按题干内容在 PDF 中定位并提取标准答案
# ---------------------------------------------------------------------------
def find_solution_in_pdf(q, progress_cb=None, max_pages: int | None = None) -> dict | None:
    """在扫描解析 PDF 中定位 q 的标准答案/解析。

    Args:
        q: QuestionItem
        progress_cb: 可选回调 (done, total, label)
        max_pages: 章节范围内最多扫描页数（None 不限制）

    Returns:
        {"answer": str, "solution": str, "page": int(0-based)} 或 None
    """
    path = _pdf_file()
    if not path or not _api_key():
        return None
    # 查询缓存命中 → 零成本复用（同题不重复扫描 PDF）
    _ckey = _lookup_key(q)
    try:
        _hit = _load_lookup_cache().get(_ckey)
        if _hit and (_hit.get("answer") or "").strip() and (_hit.get("solution") or "").strip():
            return {**_hit, "cached": True}
    except Exception:
        pass
    try:
        index = get_chapter_index()
    except Exception:
        return None
    rng = _chapter_page_range(q, index)
    if not rng:
        return None  # 章节定位失败：不全书硬扫（354 页代价高）

    start, end = rng
    if max_pages and (end - start + 1) > max_pages:
        end = start + max_pages - 1
    total = end - start + 1

    for i, pno in enumerate(range(start, end + 1)):
        if progress_cb:
            progress_cb(i + 1, total, f"扫描解析PDF 第{pno + 1}页")
        try:
            b64 = _render_b64(pno)
            if not b64:
                continue
            opts = "\n".join(q.options) if q.options else ""
            text = _vision_ask(
                [b64],
                f"【待查找的题目】\n{q.stem}\n{opts}\n\n"
                "这是考研数学解析书的扫描页。请判断本页是否包含上述题目的【标准答案或解题解析】"
                "（题目内容相同或高度相似即可，题号不必一致）。\n"
                "若包含：请严格输出：\n【命中】是\n【答案】：最终答案（选择题给选项字母，解答题给最终结果，用 LaTeX）\n"
                "【解析】：完整解题过程（公式用 LaTeX）\n"
                "若本页不包含：请只输出三个字：不包含",
            )
        except Exception:
            text = ""
        if not text or "不包含" in text:
            continue

        # 命中 → 先只提取本页（B3：多数答案单页可解，省一次视觉调用）；
        # 提取不完整（缺【答案】或【解析】）才追加下一页（兼容跨页解析）
        try:
            extract = _vision_ask(
                [b64],
                f"【题目】\n{q.stem}\n{opts}\n\n"
                "已确认本页包含该题。请提取该题的【标准答案】与【完整解题解析】，公式用 LaTeX，格式：\n"
                "【答案】：...\n【解析】：...",
            )
            _ext_has_ans = bool(re.search(r"【答案】(?:[:：])?\s*[^\n【]", extract or ""))
            _ext_has_sol = bool(re.search(r"【解析】(?:[:：])?\s*.+", extract or "", re.S))
            if (not _ext_has_ans or not _ext_has_sol) and pno + 1 < index.get("total_pages", 0):
                try:
                    b64_next = _render_b64(pno + 1)
                    if b64_next:
                        extract = _vision_ask(
                            [b64, b64_next],
                            f"【题目】\n{q.stem}\n{opts}\n\n"
                            "已确认这些扫描页包含该题（答案可能跨页）。请提取该题的【标准答案】与【完整解题解析】，"
                            "公式用 LaTeX，格式：\n【答案】：...\n【解析】：...",
                        )
                except Exception:
                    pass
        except Exception:
            extract = text

        answer = ""
        m = re.search(r"【答案】(?:[:：])?\s*([^\n【]+)", extract or "")
        if m:
            answer = m.group(1).strip()
        m2 = re.search(r"【解析】(?:[:：])?\s*(.+)", extract or "", re.S)
        solution = m2.group(1).strip() if m2 else (extract or "")
        if not solution:
            continue
        _res = {"answer": answer, "solution": solution, "page": pno, "cached": False}
        try:
            _cache = _load_lookup_cache()
            _cache[_ckey] = _res
            _save_lookup_cache(_cache)
        except Exception:
            pass
        return _res

    return None
