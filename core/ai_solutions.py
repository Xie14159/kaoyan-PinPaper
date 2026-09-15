"""
考研数学智能组卷系统 - AI 名师答案解析自动补全与本地缓存服务
用于 PDF 详细解析版中题库缺失答案/解析的题目：调用 AI 名师生成答案与详细解析，
并缓存在本地 user_data/ai_solutions.json（同题只生成一次，跨刷新、跨PDF复用）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# 延迟初始化（避免 import 时依赖 cwd）
_SOLUTIONS_FILE: Path | None = None
_lock = threading.Lock()

AI_MARK = "（AI 名师生成）"

# 方向词高危题型：题干含以下词时，双模型一致/分歧都强制官方解析册核验
# （10-综合-选-09 教训：AI 把"行向量线性表示"篡改为"列向量线性表示"，生成与审核双双做错；
#   仅靠文本审核无法拦截此类"自信地错"，需要官方册作为独立权威锚点）
_DIRECTION_PAT = re.compile(
    # 强数学语义方向词：仅保留"篡改后仍自洽"的高危词，避免口语化宽词（能由/包含/属于等）误触发
    r"行向量|列向量|行空间|列空间|线性表示|线性组合|"
    r"可逆|不可逆|有解|无解|唯一解|无穷多解|"
    r"充分必要|充分不充分|充分不必要|必要不充分|等价|同解"
)


def _official_lookup(q, max_pages=4):
    """官方解析册视觉查询（带缓存；任何失败返回 None，绝不抛出——查册是增强不是阻断）。"""
    try:
        from core.pdf_answers import find_solution_in_pdf
        return find_solution_in_pdf(q, max_pages=max_pages)
    except Exception:
        return None


def _official_text(pdf_result) -> str:
    """把官方册查询结果拼成与现有格式一致的解析文本。"""
    return (
        f"### 📖 扫描版解析（DeepSeek 视觉识别 · PDF 第 {pdf_result['page'] + 1} 页）\n\n"
        f"【标准答案】：{pdf_result['answer']}\n\n"
        f"【详细解析】：\n{pdf_result['solution']}"
    )


def _cache_file() -> Path:
    global _SOLUTIONS_FILE
    if _SOLUTIONS_FILE is None:
        _SOLUTIONS_FILE = Path(__file__).resolve().parent.parent / "user_data" / "ai_solutions.json"
    return _SOLUTIONS_FILE


_CACHE_REQUIRED = ("answer", "solution", "review_status", "stem_fp")
_CACHE_DEFAULTS = {"answer": "", "solution": "", "review_status": "unchecked", "stem_fp": ""}


def _validate_and_repair(data) -> dict:
    """结构校验 + 修复：非 dict 条目剔除；缺字段补默认（宽松优先，不误伤历史数据）；answer 非 str 剔除。"""
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    dropped = 0
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            dropped += 1
            continue
        if not all(f in v for f in _CACHE_REQUIRED):
            v = {**_CACHE_DEFAULTS, **v}  # 缺字段补默认
        if not isinstance(v.get("answer"), str):
            dropped += 1
            continue
        cleaned[k] = v
    if dropped:
        print(f"[ai_solutions] 清洗缓存：剔除 {dropped} 条脏数据", flush=True)
    return cleaned


def load_cache() -> dict:
    """加载缓存；JSON 损坏时备份现场（.corrupt-<ts>.bak）并返回空，不静默丢弃数据。"""
    path = _cache_file()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as _e:
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = path.with_name(f"{path.name}.corrupt-{ts}.bak")
        try:
            path.rename(bak)
            print(f"[ai_solutions] 缓存损坏，已备份到 {bak.name}: {type(_e).__name__}: {_e}", flush=True)
        except Exception as _e2:
            print(f"[ai_solutions] 损坏缓存备份失败: {_e2}", flush=True)
        return {}
    return _validate_and_repair(data)


def save_cache(data: dict) -> None:
    """原子写缓存：写同目录临时文件 + fsync 落盘 + os.replace 原子替换（防进程中断写坏 JSON）。"""
    path = _cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".ai_solutions.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as _e:
        print(f"[ai_solutions] 缓存写入失败: {type(_e).__name__}: {_e}", flush=True)


def get_ai_solution(qid: str, stem_fp: str) -> dict | None:
    """读取某题的 AI 生成缓存。返回 {"answer":..., "solution":..., "updated_at":...} 或 None

    stem_fp: 当前题目的题干指纹（必填）。若缓存条目的 stem_fp 与当前不一致，
    说明题库版本变动导致同一 ID 指向了不同的题，缓存不可复用（返回 None）。
    """
    if not stem_fp:
        raise ValueError("get_ai_solution 必须提供非空 stem_fp")
    entry = load_cache().get(qid)
    # solution 非空即认为有效：answer 可能因 AI 输出格式而提取不到，不应丢弃整条解析
    if entry and entry.get("solution"):
        # 提供 stem_fp 时必须与缓存一致；无指纹的旧条目一律视为不匹配（防止题库重排后张冠李戴）
        if stem_fp is not None and entry.get("stem_fp") != stem_fp:
            return None
        return entry
    return None


def _stable(v) -> str:
    """枚举取 .value（如 ChapterCategory），普通值转字符串，保证指纹稳定"""
    if v is None:
        return ""
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def stem_fingerprint(q) -> str:
    """题干+选项的规范化指纹：同一道题不同版本（仅空白/大小写差异）视为相同。

    加入章节/篇型/难度，防止"题干相同但考点不同"的题共享缓存。
    """
    raw = (
        f"{_stable(getattr(q, 'chapter', ''))}|{_stable(getattr(q, 'category', ''))}|"
        f"{_stable(getattr(q, 'difficulty', ''))}|"
        f"{q.stem or ''}|{'|'.join(q.options or [])}"
    )
    norm = re.sub(r"\s+", "", raw)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


_PLACEHOLDER_ANSWER = {"略", "见解析", "无", "-", "—", "暂无"}


def _is_placeholder(s) -> bool:
    """题库占位符（如"略""略解""见详解"）视为缺失，需要 AI 补全"""
    t = (s or "").strip()
    return bool(re.fullmatch(r"[（(]?略[）)]?|略解|答案略|解析略|见解析|见详解|无答案?|暂无|暂缺|待补充|答案见解析|参考教材|.*?详见.{0,8}?(?:解析|详解|答案|教材|课本|参考书)[^。]{0,8}|请参考(?:教材|课本|解析|答案)|[-—]+", t)) or t in _PLACEHOLDER_ANSWER


_SELF_CORRECTION_PATTERNS = [
    r"等等，?这里",
    r"重新检查",
    r"需要再次确认",
    r"需要再确认",
    r"应为.{0,6}而不是",
    r"应是.{0,6}而不是",
    r"不对，?应该是",
    r"更正为",
    r"更正一下",
    r"更正[:：]",
    r"我写错",
    r"上面写错",
    r"写错了，?应",
    r"抱歉",
    r"刚才说错",
    r"以上有误",
    r"让我们重新",
    r"不过需要再次确认",
    r"其实应该是",
    r"我(?:最初|一开始|起初)(?:以为|认为|想)",
]


def _has_self_correction(text) -> bool:
    """解析正文是否含自我纠错/自我怀疑痕迹（AI 把内部思考过程泄漏进了定稿）"""
    if not text:
        return False
    for _p in _SELF_CORRECTION_PATTERNS:
        if re.search(_p, text):
            return True
    return False


def _norm_answer_letters(raw) -> str:
    """多选题答案归一化：提取全部选项字母，去重排序（AB/BA 视为同一答案，A、C 与 AC 视为同一答案）"""
    return "".join(sorted(set(re.findall(r"[A-Da-d]", raw or "")))).upper()


def _is_choice_type(question) -> bool:
    """题目是否为选择题（单选题/多选题）：矛盾答案检测仅对选择题启用（解答题多小题答案不同属正常）"""
    try:
        v = getattr(question.question_type, "value", "")
    except Exception:
        v = str(getattr(question, "question_type", ""))
    return "选" in v


def _strip_brace_env(v: str, cmd: str) -> str:
    """用配对括号剥离 LaTeX 环境包裹（支持嵌套 \frac{}{}）：
    \boxed{...} / \text{...} 的内容替换为裸内容，循环直到无包裹"""
    pat = "\\\\" + cmd + r"\s*\{"
    while True:
        m = re.search(pat, v)
        if not m:
            break
        seg = v[m.end():m.end() + 400]
        depth = 1
        end = -1
        for _k, _ch in enumerate(seg):
            if _ch == "{":
                depth += 1
            elif _ch == "}":
                depth -= 1
                if depth == 0:
                    end = _k
                    break
        if end < 0:
            break
        v = v[:m.start()] + seg[:end] + v[m.end() + end + 1:]
    return v


def _norm_answer_zone_value(raw: str) -> str:
    """答案区块内容归一化：去 LaTeX 包裹/指令/空白/标点，取核心串（用于结构性矛盾比较）"""
    v = (raw or "").strip()
    v = re.sub(r"^\\?\[|^\\?\]|\]$|^\$|\$$", "", v.strip())
    v = _strip_brace_env(v, "boxed")
    v = _strip_brace_env(v, "text")
    v = re.sub(r"[\\{}$\[\]()（）,.。;；:：\s]+", "", v)
    return v.strip().lower()


def _answer_zone_values(text: str) -> list:
    """提取正文所有答案区块（【标准答案】/【答案】/【最终答案】/【参考答案】）的归一化值"""
    vals = []
    for mm in re.finditer(r"【(?:标准|最终|参考)?答案】\s*[:：]?\s*", text or ""):
        seg = (text or "")[mm.end():mm.end() + 600]
        cands = []
        line = seg.split("\n", 1)[0].strip()
        if line:
            cands.append(line)
        parts = []
        for ln in seg.split("\n")[1:]:
            t = ln.strip()
            if not t or t.startswith("【") or t.startswith("---") or t in ("]", "["):
                break
            parts.append(t)
        if parts:
            cands.append(" ".join(parts))
        for c in cands:
            n = _norm_answer_zone_value(c)
            if n:
                vals.append(n)
                break
    return vals


def _answer_zone_conflict(text) -> bool:
    """解析正文是否出现结构性答案矛盾：多个答案区块并存且内容互不相同
    （如【标准答案】：发散 + 【最终答案】：3/2-1/ln2 —— 先给 X 后改 Y 的结构性纠错痕迹）。

    与 _has_self_correction（措辞检测）互补：AI 可以不用"等等/重新检查"措辞，
    但【标准答案】与【最终答案】区块内容不同即为"先错后改"且正文未清理干净。
    不限题型（填空题/解答题同样适用）；诚实兜底"无法确定"多个区块值相同 → 不冲突。
    """
    if not text:
        return False
    vals = _answer_zone_values(text)
    # 编号型答案区块（多小题解答题："【答案】(1) xxx""【答案】1. xxx"）→ 各小题答案不同属正常，跳过比较
    # 编号型答案区块：数字后须跟明确编号标点且后接空白/冒号/行尾（防"0.5""1/2"这类数字答案误判为编号）
    if re.search(r"【(?:标准|最终|参考)?答案】\s*[:：]?\s*[（(]?\d+\s*[)）.)](?:\s|[:：]|$)", text):
        return False
    # 全部区块都是诚实兜底措辞（"无法确定/不确定/存疑/暂无/难以判断"）→ 不算矛盾
    if vals and all(any(x in v for x in ("无法确定", "不确定", "存疑", "暂无", "难以判断")) for v in vals):
        return False
    uniq = []
    for v in vals:
        if v not in uniq:
            uniq.append(v)
    return len(uniq) >= 2


def _clean_final_answer(raw: str) -> str:
    r"""【最终答案】区块内容清理：去 \[ \] $、\boxed{...}、\text{...} 包裹，保留可读 LaTeX 核心"""
    v = (raw or "").strip().rstrip("。．. ")
    v = re.sub(r"^\\?\[\s*", "", v.strip())
    v = re.sub(r"\s*\\?\]$", "", v)
    # 去掉所有首尾 $（$$...$$ / $...$），再剥 \boxed 包裹（顺序不能反：带 $ 时 \boxed 正则匹配不了）
    v = re.sub(r"^\$+|\$+$", "", v.strip())
    v = re.sub(r"^\\boxed\{(.*)\}$", r"\1", v.strip(), flags=re.S)
    if v.startswith("\\text{"):
        # 仅当 \\text{...} 完整包裹整个答案时剥外层（配对扫描找首 { 的闭合 }；
        # 防止 "\\text{最小值}\\frac{1}{2}" 这类"说明+公式"被误剥损坏）
        depth, end = 0, -1
        for i, ch in enumerate(v):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == len(v) - 1:
            v = v[6:end]
    return v.strip()


def _multi_answer_conflict(text, is_choice=True) -> bool:
    """解析正文是否出现多个互相矛盾的【答案】选项字母（先给X后改Y）。

    三种格式统一提取：冒号式【答案：C】/ 无冒号式【答案】C / 加粗式**答案：C**；
    AB/BA 归一化后比较；仅选择题启用（解答题多小题给不同答案属正常）；
    选择题正文出现两个不同答案（无论距离远近）即视为"先给X后改Y"的纠错痕迹。
    """
    if not text or not is_choice:
        return False
    _pats = [
        r"【答案\s*[:：]\s*([A-Da-d](?:[、,，和及与]\s*[A-Da-d]){1,3}|[A-Da-d]{1,4})(?:】|,|，|\.|。|\n|$)",
        r"【(?:标准)?答案】\s*([A-Da-d](?:[、,，和及与]\s*[A-Da-d]){1,3}|[A-Da-d]{1,4})(?:,|，|\.|。|\n|$|…)",
        r"\*\*\s*答案\s*[:：]?\s*([A-Da-d](?:[、,，和及与]\s*[A-Da-d]){1,3}|[A-Da-d]{1,4})[^\*]{0,3}\*\*",
    ]
    matches = []
    for _p in _pats:
        for m in re.finditer(_p, text):
            letters = _norm_answer_letters(m.group(1))
            if letters:
                matches.append((m.start(), m.end(), letters))
    if len(matches) < 2:
        return False
    # 选择题：正文出现两个不同答案即冲突（取消间距豁免——一道选择题不可能有两个相距很远的独立答案，
    # 远距离矛盾同样是"先给X后改Y"的纠错痕迹，应打回重生成）；解答题已被 is_choice 排除）
    u = {x[2] for x in matches}
    return len(u) > 1


def needs_solution(q) -> bool:
    """题目是否缺少答案或解析（需要 AI 补全）；空串与占位符（略/见解析）视为缺失"""
    has_answer = bool((q.answer or "").strip()) and not _is_placeholder(q.answer)
    has_solution = bool((q.solution or "").strip()) and not _is_placeholder(q.solution)
    return not (has_answer and has_solution)


_DOUBT_ANS_WORDS = ("无法确定", "不确定", "存疑", "暂无", "难以判断",
                  "无法判断", "不能确定", "待定", "暂缺", "见解析", "见详细解析")
_DOUBT_ANS_EXACT = {"略", "无", "（无）", "(无)"}


def _is_doubt_answer(ans: str) -> bool:
    """判断提取出的答案是否为"存疑/占位"性质（含补全黑名单词或精确命中占位）"""
    a = (ans or "").strip()
    if not a:
        return True
    if a in _DOUBT_ANS_EXACT:
        return True
    return any(w in a for w in _DOUBT_ANS_WORDS)


_BACKFILL_BAD_CTX = ("错误", "误认为", "易错", "陷阱", "常见错误", "需注意", "但需注意")


def _backfill_ok(v: str) -> bool:
    """backfill 提取结果的合格性：非存疑词、非否定/纠错上下文"""
    if not v or _is_doubt_answer(v):
        return False
    return not any(w in v for w in _BACKFILL_BAD_CTX)


def _backfill_extract(text: str, is_choice: bool = False) -> str:
    """S8 补全专用宽松提取：缓存已含完整解析但 answer 字段为空时，
    从解析中提取最终答案（不因后文出现"最终答案"字样而跳过中间判断）。
    仅用于已审核通过的缓存条目补全 answer 字段。
    过滤原则：宁可提取失败（留空，不烧钱），不可把否定/纠错/存疑内容当答案固化。"""
    if not (text or "").strip():
        return ""
    # 1) 选择题：最后的【答案：X】（定稿在最后）
    if is_choice:
        ms = list(re.finditer(r"【答案\s*[:：]\s*([A-Da-d](?:[、,，和及与]\s*[A-Da-d]){1,3}|[A-Da-d]{1,4})(?:】|,|，|\.|。|\n|$)", text))
        if ms:
            v = _norm_answer_letters(ms[-1].group(1))
            return v if _backfill_ok(v) else ""
    # 2) 最后的【最终答案】：X
    ms = list(re.finditer(r"【最终答案】\s*[:：]?\s*([^\n【】]{1,120})", text))
    if ms:
        v = _clean_final_answer(ms[-1].group(1))
        if v and _backfill_ok(v):
            return v
    # 3) 最后的【标准答案】/【答案】：X（同行式；定稿在最后）
    ms = list(re.finditer(r"【(?:标准)?答案】\s*(?:：|:)\s*([^\n【】]{1,120})", text))
    if ms:
        v = _clean_final_answer(ms[-1].group(1))
        if v and _backfill_ok(v):
            return v
    # 4) 标题换行式【标准答案】\n\n 答案内容（解答题常见）
    m = re.search(r"#*\s*【(?:标准)?答案】\s*[:：]?\s*\n+\s*([\s\S]{1,200}?)(?=\n\s*(?:\n|【)|$)", text)
    if m:
        v = _clean_final_answer(m.group(1))
        if v and _backfill_ok(v):
            return v
    return ""


def split_answer_from_text(text: str, is_choice=None) -> str:
    """从 AI 输出中提取简短的最终答案（供【参考答案】栏显示）"""
    # 0) 文末定稿式：【最终答案】：xxx（AI 定稿格式；正文可能先给过中间/错误答案，最终定稿最权威）
    #    取最后一个匹配；支持同行式与换行式（LaTeX 块）
    _final_matches = list(re.finditer(r"【最终答案】\s*[:：]?\s*([^\n【】]{1,200})", text))
    if _final_matches:
        ans = _clean_final_answer(_final_matches[-1].group(1))
        if ans:
            return ans
    _final_m = re.search(r"【最终答案】\s*[:：]?\s*\n+\s*([\s\S]{1,400}?)(?=\n\s*(?:\n|【)|$)", text)
    if _final_m:
        ans = _clean_final_answer(_final_m.group(1))
        if ans:
            return ans
    # 1) 选择题：【答案：C】/【答案:AB】/【答案：A、C】（必须有冒号；选项后限标点/换行，防解答题"【答案】A 是错的"误匹配）
    #    取最后一个【答案：X】：AI 内部可能先给中间结论再纠正（先B后A），最终定稿在最后；
    #    多选题顿号分隔（A、C）与连续字母（AC）统一归一化
    _ans_matches = list(re.finditer(r"【答案\s*[:：]\s*([A-Da-d](?:[、,，和及与]\s*[A-Da-d]){1,3}|[A-Da-d]{1,4})(?:】|,|，|\.|。|\n|$)", text))
    if _ans_matches:
        m = _ans_matches[-1]
        return _norm_answer_letters(m.group(1))
    # 1b) 【答案】C（无冒号；选项后必须是标点/换行；且紧邻选项处不得出现否定/转折/方法字样，防解答题误提取）
    m = re.search(r"【(?:标准)?答案】\s*([A-Da-d]{1,4})(?:,|，|\.|。|\n|\s|$)", text)
    if m:
        # 紧邻选项后（只跳过空白，不剥标点）的首字符若为否定/转折/方法字头 → 疑似纠错或解答题说明，不提取。
        # 标点隔开（"【答案】A。但解析…"）不算紧贴，避免误伤正常答案
        rest = text[m.end(1):].lstrip()
        # 防护字头：否定/转折/方法/指代等 → 疑似纠错或说明，不提取；"是正确的/是正确答案"不拦截（防误伤）
        if rest and re.match(r"^(?:不|错|非|否|但|方|其实|应该|是指|是错|是不|而|选项错|选项不|选项有误)", rest):
            pass
        else:
            return m.group(1).upper()
    # 1c) 加粗式：**答案：D** / **答案 D**
    m = re.search(r"\*\*\s*答案\s*[:：]?\s*([A-Da-d]{1,4})[^\*]{0,3}\*\*", text)
    if m:
        return m.group(1).upper()
    # 2) 标题换行式：### 【标准答案】\n\n 答案内容（解答题常见；取到空行/下一字段/文末，上限 300 字符防吞解析）
    m = re.search(r"#*\s*【(?:标准)?答案】\s*[:：]?\s*\n+\s*([\s\S]{1,300}?)(?=\n\s*(?:\n|【)|$)", text)
    if m:
        ans = m.group(1).strip().rstrip("。．. ")
        # 后文还有答案定稿（【答案】区块 / "最终答案" / "正确答案"）→ 当前【标准答案】是中间答案（先错后改），跳过
        if ans and re.search(r"【(?:标准|最终|参考)?答案】|最终答案|正确答案", text[m.end():]):
            pass
        elif ans:
            # 参考答案栏展示：统一清理 LaTeX 定界符（$ / \\boxed / \\text），防 KaTeX 定界符被字面显示
            return _clean_final_answer(ans)[:150]
    # 3) 同行式：【标准答案】：xxx
    m = re.search(r"【(?:标准)?答案】?\s*(?:：|:)\s*([^\n【】]{1,80})", text)
    if m:
        ans = m.group(1).strip().rstrip("。．. ")
        # 后文还有答案定稿（【答案】区块 / "最终答案" / "正确答案"）→ 当前是中间答案，跳过
        if ans and re.search(r"【(?:标准|最终|参考)?答案】|最终答案|正确答案", text[m.end():]):
            pass
        elif ans:
            # 参考答案栏展示：统一清理 LaTeX 定界符
            return _clean_final_answer(ans)
    # 3.5) 无【】"最终答案"定稿行（先错后改文本的最终值；取最后一个，_clean_final_answer 去 LaTeX 包裹）
    _final_rows = list(re.finditer(r"最终答案\s*(?:为|是|[:：])\s*([^\n【】]{1,150})", text))
    if _final_rows:
        _ans35 = _clean_final_answer(_final_rows[-1].group(1))
        if _ans35:
            return _ans35
    # 4) 诚实兜底：AI 明确表示无法确定 → 原样返回（让 PDF 如实显示，不冒充答案）
    m = re.search(r"【(?:标准)?答案】\s*[:：]?\s*(无法确定|不确定|存疑|暂无)", text)
    if m:
        return m.group(1)
    # 5.5) 填空题/解答题公式答案：AI 定稿常用 \boxed{...} 包裹最终答案
    #      （"故应填：$$\boxed{\frac32-\frac{1}{\ln 2}}$$"）；取最后一个 \boxed（文末定稿），
    #      括号配对处理嵌套 \frac{}{}；仅 is_choice=False（非选择题）启用，避免干扰选项字母分支
    if is_choice is False:
        _boxed = list(re.finditer(r"\\boxed\s*\{", text))
        # 仅当 \boxed 出现在文末（距末尾 < 200 字符）才启用：
        # 推导中间的高亮 \boxed（如"由夹逼定理得 \boxed{0<=I<=0}"）不是最终答案，不提取
        if _boxed and len(text) - _boxed[-1].start() < 200:
            _b = _boxed[-1]
            _seg = text[_b.end():_b.end() + 400]
            # 配对法：depth 从 1 起算（\boxed{ 的 { 计入），正确停在 \boxed 自己的闭合 }
            _depth = 1
            _end = -1
            for _k, _ch in enumerate(_seg):
                if _ch == "{":
                    _depth += 1
                elif _ch == "}":
                    _depth -= 1
                    if _depth == 0:
                        _end = _k
                        break
            if _end > 0:
                # 尾部中文检测：\boxed 闭合后若紧跟中文正文（如"综上所述…""其中…需要讨论"）
                # → 是推导中间的高亮结论，非文末定稿，不提取；
                # 允许 LaTeX 公式延续（$$、\quad、\text{中文} 等）
                _after = _seg[_end + 1:_end + 80]
                _after_clean = re.sub(r"\\text\{[^}]*\}", "", _after)
                if re.search(r"[\u4e00-\u9fff]", _after_clean):
                    pass  # 中文正文 → 中间高亮，跳过
                else:
                    _ans = _clean_final_answer(_seg[:_end])
                    if _ans:
                        return _ans
    # 5) 自然语言结论式（AI 定稿解析常见："综上，正确选项为 C""因此选 A""答案为 A""$\boxed{\text{A}}$"）
    #    仅选择题启用（is_choice=False 时解答题答案可能是数学表达式/变量，不走选项字母提取）；
    #    强结论前缀（综上/最终/结论为）允许直接接答案；一般前缀（因此/所以/故/即/于是）须带"选/答案"词；
    #    多字母否定前瞻排除"综上，A、B 不成立 / B、C 不符合 / A、B、C 都不成立"式排除法句；
    #    提取后二次校验拒绝变量/讨论场景："答案为 a 的平方""故选 A 作为反例""答案为 A 而 B 才是"
    if is_choice is not False:
        _nl_gap = r"(?:应?选|选择|答案填|应填|本题选|正确(?:的)?(?:选项|答案)|答案为?|答案就是|正确答案)\s*[为是:：]?\s*"
        _nl_ans = r"[（(]?[$]{0,2}\s*(?:\\boxed\{)?\s*(?:\\text\{)?\s*([A-Da-d]{1,4}(?:[、，,，和及与\s]*[A-Da-d]{1,4}){0,3})"
        _nl_neg = r"(?![、，,，\s]*(?:[A-Da-d][、，,，\s]*){0,3}(?:都\s*)?(?:不|非|错|排除|不符|项错|项不))"
        _nl_patterns = [
            (r"(?:综上|最终|结论(?:为|是))\s*[，,]?\s*(?:" + _nl_gap + r")?\s*" + _nl_ans + _nl_neg, False),
            (r"(?:因此|所以|故|即|于是)\s*[，,]?\s*" + _nl_gap + _nl_ans, False),
            (r"(?:答案为?|答案就是|正确答案(?:为|是)|正确(?:的)?选项(?:为|是)|答案[:：]|答案填|应填|本题选)\s*" + _nl_ans, False),
            (r"(?:应?选|选择)\s*" + _nl_ans, True),
            (r"([A-Da-d])\s*(?:为|是)\s*正确(?:选项|答案)", False),
        ]
        for _pat, _is_sel in _nl_patterns:
            m = re.search(_pat, text)
            if not m:
                continue
            ans = re.sub(r"[^A-Da-d]", "", m.group(1)).upper()
            if not ans:
                continue
            _post = text[m.end(1):m.end(1) + 5].lstrip()
            # 无前缀"选/选择"类：字母后紧跟"，则/，说明"等讨论 → 拒绝（"若选 A，则单调递增"）
            if _is_sel and re.match(r"[，,、][\u4e00-\u9fff]", _post):
                continue
            # 二次校验：字母后紧跟汉字且非"项/选项/为/是/和/与/及"开头 → 是变量/讨论
            # （"答案为 a 的平方""故选 A 作为反例""答案为 A 而 B 才是"拒绝；
            #  "C 为正确选项""A 和 B"放行）
            # 白名单"是"的否定变体先拦截（"答案为 A 是错的"拒绝；"A 是正确的"放行）
            if _post.startswith(("是错", "是不", "是错误", "是不对")):
                continue
            if _post and re.match(r"[\u4e00-\u9fff]", _post) and not _post.startswith(("项", "选项", "为", "是", "和", "与", "及")):
                continue  # 本模式被拒绝 → 尝试下一模式（如"故选 C"）
            norm = _norm_answer_letters(ans)
            if norm:
                return norm
    return ""


def _retry_review(q, entry, ai_tutor=None):
    """对上次审核失败（网络抖动等）的缓存条目重新审核，更新状态（复用原答案，不重新生成）

    ai_tutor: 生成 tutor，作为无独立审核配置时的回退（review_solution 内部已对 None 防御）。
    注意：锁内合并写入，只更新审核相关字段，防止并发写入覆盖最新解析（第五轮审查确认的竞态）。
    """
    try:
        from core.ai_review import review_solution

        sol = entry.get("solution") or ""
        if not sol.strip():
            return
        review = review_solution(q, sol, ai_tutor)
        entry2 = dict(entry)
        entry2["stem_fp"] = stem_fingerprint(q)
        if review["verdict"] == "verified":
            entry2["review_status"] = "verified"
        elif review["verdict"] == "fixed" and review.get("corrected_text"):
            entry2["solution"] = review["corrected_text"]
            entry2["answer"] = split_answer_from_text(review["corrected_text"], is_choice=_is_choice_type(q)) or entry.get("answer") or ""
            entry2["review_status"] = "fixed"
        else:
            # unknown/存疑 → 标"未审核"（与主流程 S5 一致；不做视觉核验，避免重审路径复杂化）
            entry2["review_status"] = "unchecked"
        entry2["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            data = load_cache()
            cur = data.get(q.id) or {}
            cur.update({
                "review_status": entry2["review_status"],
                "reviewed_at": entry2["reviewed_at"],
                "stem_fp": entry2["stem_fp"],
            })
            # 版本校验：仅当锁内读取的解析与本次审核时一致，才写修正版
            # （防并发期间另一路径已更新解析，用旧审核结果覆盖新版本）
            if entry2.get("solution") and cur.get("solution") == entry.get("solution"):
                cur["solution"] = entry2["solution"]
                cur["answer"] = entry2["answer"]
            data[q.id] = cur
            save_cache(data)
    except Exception as _e:
        print(f"[ensure_solutions] 重审失败 qid={q.id}: {type(_e).__name__}: {_e}", flush=True)


def ensure_solutions(questions, ai_tutor, progress_cb=None, max_workers=4) -> tuple[int, int]:
    """为缺失答案/解析的题目生成 AI 解析，并写入本地缓存。

    并发版（优化 3）：ThreadPoolExecutor 并行处理每题完整管线（生成→审核→提取→写缓存）。
    - AITutor 无共享可变状态（每次调用独立 urllib 请求），线程安全；写缓存已有 _lock 保护。
    - Streamlit 约束：st.progress 必须在主线程调用 → workers 只更新线程安全共享状态
      （done 计数 + 每题当前状态），主线程轮询（每 0.15s）调 progress_cb 更新进度条。
    - 每题内部保持串行（生成→审核依赖不变）；缓存命中题不占线程。
    - worker 异常记入 errors（不中断其他题），主线程统一收集打印。
    - max_workers：并发数（默认 4；中转限流频繁时可调 1 退化为串行）。

    Args:
        questions: QuestionItem 列表
        ai_tutor: AITutor 实例（已配置 API Key）
        progress_cb: 可选回调 (done, total, qid, status)
        max_workers: 并发数

    Returns:
        (本次新生成数, 复用缓存数)
    """
    missing = [q for q in questions if needs_solution(q)]
    if not missing:
        return 0, 0
    total = len(missing)

    _ps: dict = {"lock": threading.Lock(), "done": 0, "current": {}, "errors": {}, "last": ("", "")}

    def _report(qid: str, status: str) -> None:
        """worker 内记录中间状态（不碰 Streamlit）"""
        with _ps["lock"]:
            _ps["current"][qid] = status

    def _finish(qid: str, status: str) -> None:
        """worker 内标记一题完成"""
        with _ps["lock"]:
            _ps["done"] += 1
            _ps["current"].pop(qid, None)
            _ps["last"] = (qid, status)

    def _process_one(q):
        try:
            # 已有缓存 → 直接复用（校验题干指纹：题库版本变动后旧缓存作废）
            existing = get_ai_solution(q.id, stem_fp=stem_fingerprint(q))
            _old_retry = existing.get("retry_count", 0) if existing else 0
            _retry_cnt = 0
            if existing and existing.get("review_status") == "human":
                # 人工核对过的条目最高优先：不受 S8 清除/存疑重试影响
                _finish(q.id, "cached"); return "cached"
            # 未审核条目（AI 名师按钮生成等）→ 复用解析但重审一次（不重新生成），
            # 最多重审 1 次防反复调用；重审后转 verified/fixed/doubt
            if existing and existing.get("review_status") == "unchecked" and (existing.get("solution") or "").strip():
                if existing.get("review_retries", 0) < 1:
                    try:
                        _retry_review(q, existing, ai_tutor)
                        with _lock:
                            _c = load_cache()
                            if q.id in _c:
                                _c[q.id]["review_retries"] = existing.get("review_retries", 0) + 1
                                save_cache(_c)
                    except Exception:
                        pass
                _finish(q.id, "cached"); return "cached"
            # S8：题库缺答案且缓存也缺答案 → 优先从已有解析提取答案补全缓存
            # （防"AI 已生成解析但答案提取失败"的题每次下载都重新生成+审核，重复烧钱）
            # 提取成功：补 answer 落盘，走复用；提取失败/存疑：保留缓存复用（不重新生成），
            # 这是核心省钱策略——复用解析，参考答案栏空时显示"见详细解析"。
            if existing and not (q.answer or "").strip() and not (existing.get("answer") or "").strip():
                _extracted_ans = ""
                try:
                    _extracted_ans = (_backfill_extract(existing.get("solution") or "", _is_choice_type(q)) or "").strip()
                except Exception:
                    _extracted_ans = ""
                if _extracted_ans and not _is_doubt_answer(_extracted_ans):
                    try:
                        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with _lock:  # 并发安全：load→modify→save 全程持锁
                            _c = load_cache()
                            _e = _c.get(q.id)
                            if _e is None:
                                _e = dict(existing)
                                _c[q.id] = _e
                            _e["answer"] = _extracted_ans
                            _e["answer_source"] = "backfill"
                            _e["updated_at"] = _now
                            save_cache(_c)
                        existing["answer"] = _extracted_ans
                        existing["answer_source"] = "backfill"
                        print(f"[ai_solutions] S8 补全答案: {q.id} → {_extracted_ans[:40]}", flush=True)
                    except Exception as _e2:
                        print(f"[ai_solutions] S8 补全写缓存失败 {q.id}: {type(_e2).__name__}: {_e2}", flush=True)
                    # 答案已补全 → 继续走下方复用流程（不重新生成、不重复审核）
                else:
                    # 提取失败/答案为存疑词 → 记录一次尝试（可审计），保留缓存复用
                    # （不置 existing=None，不重新生成、不重复审核，避免反复烧钱）；
                    # PDF 参考答案栏对该题显示"见详细解析"
                    try:
                        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with _lock:
                            _c = load_cache()
                            _e = _c.get(q.id)
                            if _e is not None and not _e.get("backfill_attempted"):
                                _e["backfill_attempted"] = True
                                _e["updated_at"] = _now
                                save_cache(_c)
                    except Exception as _e3:
                        print(f"[ai_solutions] S8 补全尝试标记失败 {q.id}: {type(_e3).__name__}: {_e3}", flush=True)
            # 上次 AI 诚实存疑（"无法确定"等）→ 重新生成尝试，最多 1 次
            # （用户原则"宁可存疑不瞎填"，重试大概率同样存疑；1 次封顶防反复烧钱）
            if existing and any(x in (existing.get("answer") or "") for x in ("无法确定", "不确定", "存疑", "暂无", "难以判断")):
                if _old_retry < 1:
                    existing = None
                    _retry_cnt = _old_retry + 1
                # 已重试满 3 次仍存疑 → 直接复用（AI 确实解不出，不再反复等待）
            if existing:
                # 上次审核失败（网络抖动等）→ 复用答案但重新审核一次（不重新生成）
                if existing.get("review_status") == "review_failed" and existing.get("review_retries", 0) < 1:
                    try:
                        _retry_review(q, existing, ai_tutor)
                        with _lock:
                            _c = load_cache()
                            if q.id in _c:
                                _c[q.id]["review_retries"] = existing.get("review_retries", 0) + 1
                                save_cache(_c)
                    except Exception:
                        pass
                    _finish(q.id, "cached"); return "cached"
                elif existing.get("review_status") == "review_failed":
                    # 已重审满 1 次 → 直接复用，不再反复重审
                    _finish(q.id, "cached"); return "cached"
                _finish(q.id, "cached"); return "cached"

            # 调用 AI 名师生成（非流式，兼容 Claude 中转端点；流式经该中转返回空）
            try:
                full_text = ai_tutor.solve_question(q)
            except Exception as _e:
                print(f"[ensure_solutions] 生成失败 qid={q.id}: {type(_e).__name__}: {_e}", flush=True)
                full_text = ""

            # 生成后自检：正文含自我纠错痕迹/多个矛盾答案 → 打回重新生成一次（定稿强化版）
            # （AI 内部"先得出X、再纠正为Y"的思考过程不得写进最终解析；重试后仍脏 → 送审时审核侧必降级，不静默放行）
            if full_text and not any(k in full_text for k in ("【API 错误】", "【网络异常】", "【提示】未配置")):
                _is_choice = _is_choice_type(q)
                if _has_self_correction(full_text) or _multi_answer_conflict(full_text, _is_choice) or _answer_zone_conflict(full_text):
                    try:
                        full_text = ai_tutor.solve_question(q, final_draft=True)
                    except Exception:
                        pass
                    if _has_self_correction(full_text) or _multi_answer_conflict(full_text, _is_choice) or _answer_zone_conflict(full_text):
                        print(f"[ensure_solutions] 重试后仍含自我纠错痕迹 qid={q.id}，交由审核降级兜底", flush=True)

            # 调用失败（未配置 Key / 网络异常 / API 错误）→ 尝试从扫描版解析 PDF 视觉定位标准答案
            if not full_text or any(k in full_text for k in ("【API 错误】", "【网络异常】", "【提示】未配置")):
                review_status = "pdf"
                pdf_result = None
                try:
                    from core.pdf_answers import find_solution_in_pdf

                    def _pdf_cb(done, total, label):
                        # 详情经 pdf_detail: 前缀透传
                        _report(q.id, f"pdf_detail:{label}")

                    # B2: 单题最多扫 6 页，封顶视觉调用成本（题目靠后时前 N-1 页"不包含"白扫）
                    pdf_result = find_solution_in_pdf(q, progress_cb=_pdf_cb, max_pages=6)
                except Exception:
                    pdf_result = None

                if pdf_result and (pdf_result.get("answer") or "").strip() and not _is_placeholder(pdf_result.get("answer")) and (pdf_result.get("solution") or "").strip() and pdf_result.get("page") is not None:
                    full_text = (
                        f"### 📖 扫描版解析（DeepSeek 视觉识别 · PDF 第 {pdf_result['page'] + 1} 页）\n\n"
                        f"【标准答案】：{pdf_result['answer']}\n\n"
                        f"【详细解析】：\n{pdf_result['solution']}"
                    )
                else:
                    # 视觉兜底也失败：写入占位缓存，避免每次下载都重复调用 API
                    full_text = "【提示】该题暂无可用解析，请参考教材或咨询老师。"
                    review_status = "failed"
                _finish(q.id, "failed"); return "failed"
            else:
                # AI 名师生成成功 → 独立审核（阅卷专家重算 + 对照）
                _report(q.id, "reviewing")
                review_status = "unchecked"
                try:
                    from core.ai_review import review_solution

                    review = review_solution(q, full_text, ai_tutor)
                    # 确定性结构矛盾检测（不依赖模型判断）：【标准答案】与【最终答案】区块并存且互不相同
                    # （先给 X 后改 Y 的结构性纠错痕迹）→ 无论模型 verdict 如何都不得 verified
                    if _answer_zone_conflict(full_text):
                        print(f"[ensure_solutions] 结构性答案矛盾 qid={q.id}，强制降级 doubt（禁止 verified）", flush=True)
                        review = {"verdict": "doubt", "corrected_text": None}
                    # 生成侧是否诚实存疑（两模型第四轮审查确认：fixed 分支也必须考虑生成侧存疑，
                    # 防止审核误判的错误修正覆盖诚实的"无法确定"）
                    _gen_uncertain = any(x in full_text for x in ("无法确定", "不确定", "存疑", "暂无", "难以判断"))
                    if review["verdict"] == "verified" and not _gen_uncertain:
                        review_status = "verified"
                        # 方向词高危题：双模型一致也可能一起错（10-综合-选-09）→ 官方册确认
                        if _DIRECTION_PAT.search(q.stem):
                            _pdf = _official_lookup(q, max_pages=4)
                            if _pdf and (_pdf.get("answer") or "").strip() and not _is_placeholder(_pdf["answer"]):
                                if _is_choice_type(q) and _pdf["answer"].strip() in "ABCD":
                                    _gen_ans = split_answer_from_text(full_text, is_choice=True) or ""
                                    if _gen_ans and _gen_ans != _pdf["answer"].strip():
                                        # 官方册与生成不一致：官方是权威，以官方册为准
                                        full_text = _official_text(_pdf)
                                        review_status = "pdf_fixed"
                                        print(f"[ensure_solutions] 方向词题官方册仲裁 qid={q.id}: 生成{_gen_ans}→官方{_pdf['answer'].strip()}", flush=True)
                    elif review["verdict"] == "fixed" and review.get("corrected_text"):
                        full_text = review["corrected_text"]
                        # 审核修正也可能错（DeepSeek 曾错改 01-基础-选-04；10-综合-选-09 审核给出错误修正 B）
                        # 方向词题 → 官方册仲裁：选择题直接采用官方答案与解析
                        if _DIRECTION_PAT.search(q.stem):
                            _pdf = _official_lookup(q, max_pages=4)
                            if _pdf and (_pdf.get("answer") or "").strip() and not _is_placeholder(_pdf["answer"]):
                                if _is_choice_type(q) and _pdf["answer"].strip() in "ABCD":
                                    full_text = _official_text(_pdf)
                                    review_status = "pdf_fixed"
                                    print(f"[ensure_solutions] 方向词题审核修正被官方册覆盖 qid={q.id} → 官方{_pdf['answer'].strip()}", flush=True)
                                elif not _gen_uncertain:
                                    review_status = "fixed"
                                else:
                                    review_status = "doubt"
                            else:
                                # 官方册查不到（未收录/视觉失败）：双模型已分歧，方向词高危题
                                # 不得冒充"已审核修正"——降级 doubt（待核实），宁缺毋滥
                                print(f"[ensure_solutions] 方向词题官方册未命中 qid={q.id}，降级 doubt（不标 fixed）", flush=True)
                                review_status = "doubt"
                        elif not _gen_uncertain:
                            review_status = "fixed"
                        else:
                            # 生成侧存疑 + 审核给出确定修正：采用修正版，但仅标"待核实"不标"已审核修正"
                            # （DeepSeek 曾错改 01-基础-选-04，审核修正也需人工留意）
                            review_status = "doubt"
                    elif review["verdict"] in ("doubt", "unknown") or _gen_uncertain:
                        # 审核存疑或响应异常 → 尝试扫描版解析 PDF 视觉核验标准答案
                        try:
                            from core.pdf_answers import find_solution_in_pdf
                            # B2: 单题最多扫 6 页
                            pdf_result = find_solution_in_pdf(q, max_pages=6)
                        except Exception:
                            pdf_result = None
                        if pdf_result and (pdf_result.get("answer") or "").strip() and not _is_placeholder(pdf_result.get("answer")) and (pdf_result.get("solution") or "").strip() and pdf_result.get("page") is not None:
                            full_text = (
                                f"### 📖 扫描版解析（DeepSeek 视觉识别 · PDF 第 {pdf_result['page'] + 1} 页）\n\n"
                                f"【标准答案】：{pdf_result['answer']}\n\n"
                                f"【详细解析】：\n{pdf_result['solution']}"
                            )
                            review_status = "pdf_fixed"
                        else:
                            review_status = "doubt" if review["verdict"] == "doubt" else "unchecked"
                    # unknown：审核响应异常，视觉核验也失败 → 保留原解答并标记未审核
                except Exception as _e2:
                    print(f"[ensure_solutions] 审核异常 qid={q.id}: {type(_e2).__name__}: {_e2}", flush=True)
                    # 标 review_failed：复用段识别后会用缓存答案重审一次（不重新生成）
                    review_status = "review_failed"
                _report(q.id, "review_failed")

            answer = split_answer_from_text(full_text, is_choice=_is_choice_type(q)) or (q.answer or "")
            # 本次生成结果仍为存疑 → 继承历史重试计数（防"每次重置0"导致无限重试）
            if any(x in (answer or "") for x in ("无法确定", "不确定", "存疑", "暂无", "难以判断")):
                if _retry_cnt == 0:
                    _retry_cnt = _old_retry + 1
            with _lock:
                data = load_cache()
                data[q.id] = {
                    "answer": answer,
                    "solution": full_text,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "review_status": review_status,
                    "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "stem_fp": stem_fingerprint(q),
                    "retry_count": _retry_cnt,
                }
                save_cache(data)
            _finish(q.id, "generated"); return "generated"

        except Exception as _e:
            with _ps["lock"]:
                _ps["errors"][q.id] = _e
                _ps["done"] += 1
                _ps["current"].pop(q.id, None)
            print(f"[ensure_solutions] 题 {q.id} 处理异常: {type(_e).__name__}: {_e}", flush=True)
            return "failed"

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_process_one, q) for q in missing]
        while True:
            with _ps["lock"]:
                done = _ps["done"]
                cur = dict(_ps["current"])
                last = _ps["last"]
            if progress_cb and (done or cur):
                if cur:
                    # 显示最近进入中间状态的题
                    qid, st_ = next(reversed(list(cur.items())))
                    progress_cb(done, total, qid, st_)
                else:
                    qid, st_ = last
                    if st_:
                        progress_cb(done, total, qid, st_)
            if all(f.done() for f in futs):
                break
            time.sleep(0.15)

    generated = cached = 0
    for f in futs:
        r = f.result() if f.done() else None
        if r == "generated":
            generated += 1
        elif r == "cached":
            cached += 1
    for qid, e in _ps["errors"].items():
        print(f"[ensure_solutions] 题 {qid} 异常: {type(e).__name__}: {e}", flush=True)
    return generated, cached
