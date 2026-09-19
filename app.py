"""
考研数学《880》智能拼好卷 & 错题标练系统
核心逻辑：先标记错题 -> 默认以错题本题源组卷重练 (亦可随时切换全书模考)
"""
from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import html
import io
import os
import random
import re
import threading
import uuid
from pathlib import Path

import streamlit as st

from core.bank_loader import BankLoader, parse_qid_tuple, parse_chapter_number
from core.models import (
    ChapterCategory,
    DifficultyLevel,
    PaperBundle,
    PaperItem,
    PaperMode,
    QuestionItem,
    QuestionType,
    SubjectType,
    MATH_1_CHAPTERS,
    MATH_2_CHAPTERS,
    MATH_3_CHAPTERS,
)
from core.paper_engine import EngineRequest, PaperEngine
from core.pdf_service import PDFEdition, PDFService
from core.ai_tutor import AITutor
from core.ai_health import probe_key
from core.ai_solutions import ensure_solutions, needs_solution
from core.state_manager import StateManager

# 日志基础设施：pythonw 后台运行无 stdout/stderr，统一写 logs/app.log（错误可见性）
import logging
_LOG_DIR = Path(__file__).resolve().parent / "logs"
if not logging.getLogger().handlers:
    try:
        _LOG_DIR.mkdir(exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.FileHandler(_LOG_DIR / "app.log", encoding="utf-8")],
        )
    except Exception:
        pass

# 题干图片 base64 解码缓存：同一 data 串只解一次（fragment 局部 rerun 时全部剩余题目
# 都会重新渲染，带图题每次都要 b64decode，缓存后从毫秒级降到微秒级）
@functools.lru_cache(maxsize=256)
def _b64decode_cached(data: str) -> bytes:
    return base64.b64decode(data)


# 题干里内联的 <img src="data:...base64,..."> 标签(loader 生成)
_INLINE_IMG_RE = re.compile(
    r'<img\s+src="data:(?P<mime>[^;]+);base64,(?P<data>[^"]+)"[^>]*>', re.S
)


# =========================================================================
# API 健康探测：后台线程 + fragment 轮询（探测绝不阻塞主脚本 rerun）
# 这是"点标错/做题后页面一直转圈"的主要修复：探测走网络、单次最多超时 8s，
# 若在主脚本同步等待，任何交互（含 on_click 回调触发的 rerun）都会卡住。
# key/base/model 变化时自动重探（避免换了 Key 仍显示旧结果）。
# =========================================================================
_bg_health: dict = {}          # _skey -> {"fp": str, "res": HealthResult|None}
_bg_health_probing: set = set()
_bg_health_lock = threading.Lock()


def _health_fp(k: str, b: str, m: str) -> str:
    _kh = hashlib.sha256((k or "").encode("utf-8")).hexdigest()[:12]
    return f"{_kh}|{b}|{m}"


def _bg_probe_worker(skey: str, api_key: str, base_url: str, model: str, force: bool = False) -> None:
    try:
        try:
            _res = probe_key(api_key, base_url, model, force=force)
        except Exception:
            _res = None
        with _bg_health_lock:
            _bg_health[skey] = {"fp": _health_fp(api_key, base_url, model), "res": _res}
    finally:
        # 无论异常类型（含 BaseException）都必须清理探测标记，否则该 skey 永久停摆
        with _bg_health_lock:
            _bg_health_probing.discard(skey)


def _start_bg_probe(skey: str, api_key: str, base_url: str, model: str, force: bool = False) -> None:
    with _bg_health_lock:
        if skey in _bg_health_probing:
            return
        _bg_health_probing.add(skey)
    threading.Thread(
        target=_bg_probe_worker, args=(skey, api_key, base_url, model, force), daemon=True
    ).start()


@st.fragment(run_every=5.0)
def render_health_panel(_targets) -> None:
    """API 状态面板：轮询后台探测结果，探测完成自动刷新；重检走后台不阻塞。"""
    for _label, _k, _b, _m in _targets:
        _skey = f"_health_{_label}"
        _cur_fp = _health_fp(_k, _b, _m)
        _ent = _bg_health.get(_skey)
        # 必须校验 fp：换 key 后、新探测未返回前，旧结果不得显示（防止"假绿"）
        _res = _ent.get("res") if (_ent and _ent.get("fp") == _cur_fp) else None
        _c1, _c2 = st.columns([4, 1])
        with _c1:
            if _res is None:
                st.caption(f"🔍 {_label}：检测中…")
            elif _res.ok:
                st.success(f"✅ {_label} 可用（{_res.latency_ms}ms）")
            elif _res.status == "auth":
                st.error(f"❌ {_label} 鉴权失败：{_res.detail}")
            elif _res.status == "model":
                st.error(f"❌ {_label} 模型无效：{_res.detail}")
            elif _res.status == "rate":
                st.warning(f"⚠️ {_label} 限流：{_res.detail}")
            else:
                st.warning(f"⚠️ {_label} 异常：{_res.detail}")
        with _c2:
            if st.button("重检", key=f"{_skey}_btn", help="强制重新探测该 Key"):
                with _bg_health_lock:
                    _bg_health.pop(_skey, None)
                _start_bg_probe(_skey, _k, _b, _m, force=True)
                st.rerun(scope="fragment")


def render_stem(text: str) -> None:
    """渲染题干:文字段走 st.markdown,内联 base64 图片走 st.image。

    Streamlit 的 markdown 消毒器会剥掉 <img src> 里的 data: URI(显示成裂图),
    故把题干按 <img> 切开,图片用原生 st.image 解码渲染,文字段仍走 markdown
    (unsafe_allow_html 供公式/管道表格)。PDF 路径不经过这里,仍用内联 HTML。
    """
    if not text:
        return
    pos = 0
    for m in _INLINE_IMG_RE.finditer(text):
        pre = text[pos:m.start()]
        if pre.strip():
            st.markdown(pre, unsafe_allow_html=True)
        try:
            st.image(_b64decode_cached(m.group("data")))
        except (binascii.Error, ValueError):
            st.caption("（图片加载失败）")
        pos = m.end()
    rest = text[pos:]
    if rest.strip() or pos == 0:
        st.markdown(rest, unsafe_allow_html=True)


def diff_badge(q) -> str:
    """难度标签 HTML(基础/综合/拓展)。真题已由大模型逐题判定真实难度,同 880 正常显示。"""
    cls = "badge-basic" if q.difficulty == DifficultyLevel.BASIC else ("badge-adv" if q.difficulty == DifficultyLevel.ADVANCED else "badge-comp")
    return f'<span class="badge {cls}">[{q.difficulty.value}]</span>'


# =========================================================================
# 1. Page Configuration
# =========================================================================
st.set_page_config(
    page_title="考研拼好卷系统 · 智能组卷与错题攻坚平台",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 2. Global Singletons & Data Loader
# =========================================================================
@st.cache_resource(show_spinner="⚡ 正在初始化 880 题库与知识图谱引擎...")
def get_bank_loader(subject: SubjectType = SubjectType.MATH_1) -> BankLoader:
    loader = BankLoader(subject=subject)
    loader.load()
    return loader


@functools.lru_cache(maxsize=16)
def _cached_questions(subject_value: str) -> tuple:
    """缓存 BankLoader.load() 结果：全量 rerun 时侧边栏不再每次重新解析题库 JSON（~227ms）。
    题库为静态文件；新增/更新题库后重启服务即刷新缓存。返回 tuple 只读引用，安全。"""
    _ld = get_bank_loader(SubjectType(subject_value))
    return tuple(_ld.load())



@st.cache_data(show_spinner=False)
def get_chapter_dist() -> dict:
    """真题章节分布模型 {科: {题型: {章名: 权重}}};文件缺失(如云端未提交)则返回空 → 特性静默不启用。"""
    p = Path(__file__).resolve().parent / "题库资料" / "真题章节分布.json"
    try:
        import json as _json
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# 运行环境判定：Streamlit Community Cloud 把应用挂载在 /mount/src/... 目录
IS_CLOUD = str(Path(__file__).resolve()).startswith("/mount")

# 身份与错题存储策略：
# - 链接带 ?u= → 一律沿用（支持"转发自己的完整链接"实现跨设备同步）
# - 云端无 ?u= → 每访客随机 u，各存各的；但云端硬盘会被清空，错题仅靠 URL 持久化，
#   故必须提示用户务必保存链接，否则记录丢失
# - 本地无 ?u= → 用固定档案名，错题长期落在本地文件，重开 localhost 即在，无需记链接
if "u" in st.query_params:
    current_user_param = st.query_params["u"]
elif "user" in st.query_params:
    current_user_param = st.query_params["user"]
elif IS_CLOUD:
    current_user_param = uuid.uuid4().hex[:8]
    st.query_params["u"] = current_user_param
else:
    current_user_param = "local"
    st.query_params["u"] = current_user_param

if "current_user_id" not in st.session_state or st.session_state.current_user_id != current_user_param:
    st.session_state.current_user_id = current_user_param
    st.session_state.state_mgr = StateManager(username=current_user_param)

state_mgr: StateManager = st.session_state.state_mgr
pdf_service = PDFService()


# AI 配置本地持久化（API Key 不放 URL，只存本机文件）
AI_CONFIG_FILE = Path(__file__).resolve().parent / "user_data" / "ai_config.json"


_ai_cfg_mtime = -1.0


@functools.lru_cache(maxsize=1)
def _load_ai_config_impl(_mtime: float) -> dict:
    import json as _json
    return _json.loads(AI_CONFIG_FILE.read_text(encoding="utf-8"))


def load_ai_config() -> dict:
    global _ai_cfg_mtime
    try:
        _mt = AI_CONFIG_FILE.stat().st_mtime
        if _mt != _ai_cfg_mtime:
            _load_ai_config_impl.cache_clear()
            _ai_cfg_mtime = _mt
        return _load_ai_config_impl(_mt)
    except Exception:
        return {}


def save_ai_config(cfg: dict) -> None:
    try:
        import json as _json
        AI_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        AI_CONFIG_FILE.write_text(_json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# 拼卷配置本地持久化（刷新页面不丢）
PAPER_CONFIG_FILE = Path(__file__).resolve().parent / "user_data" / "paper_config.json"

# 全局配置 key（不随科目变化）
_GLOBAL_CONFIG_KEYS = [
    "p1_basic", "p1_comp", "p1_adv", "p1_tag",
    "custom_qc", "custom_qf", "custom_qs",
    "c_w_math", "c_w_linalg", "c_w_prob",
]
# 按科目区分的配置 key 前缀
_SUBJECT_CONFIG_PREFIXES = [
    "p1_wrong_ratio_", "p1_exclude_seen_", "p1_use_dist_",
    "p1_dist_strength_", "p1_mode_", "p1_chk_math_",
    "p1_chk_linalg_", "p1_chk_prob_",
]


_paper_cfg_mtime = -1.0


@functools.lru_cache(maxsize=1)
def _load_paper_config_impl(_mtime: float) -> dict:
    import json as _json
    return _json.loads(PAPER_CONFIG_FILE.read_text(encoding="utf-8"))


def load_paper_config() -> dict:
    global _paper_cfg_mtime
    try:
        _mt = PAPER_CONFIG_FILE.stat().st_mtime
        if _mt != _paper_cfg_mtime:
            _load_paper_config_impl.cache_clear()
            _paper_cfg_mtime = _mt
        return _load_paper_config_impl(_mt)
    except Exception:
        return {}


def save_paper_config() -> None:
    """把当前 session_state 里的拼卷配置收集落盘。"""
    try:
        import json as _json
        cfg = {}
        # 全局 key
        for k in _GLOBAL_CONFIG_KEYS:
            if k in st.session_state:
                cfg[k] = st.session_state[k]
        # 按科目 key（遍历 session_state，匹配前缀）
        for k, v in st.session_state.items():
            for prefix in _SUBJECT_CONFIG_PREFIXES:
                if k.startswith(prefix):
                    cfg[k] = v
                    break
        PAPER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        PAPER_CONFIG_FILE.write_text(_json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# Caching PDF generation so clicking buttons/checkboxes is instantaneous (0ms)
@st.cache_data(show_spinner="⚡ 正在后台生成高清矢量 PDF 导出流...")
def get_cached_pdf(paper_id: str, title: str, q_ids: tuple[str, ...], edition_str: str = "real_exam", subject_str: str = "数学一") -> bytes:
    sub = SubjectType(subject_str) if subject_str in [s.value for s in SubjectType] else SubjectType.MATH_1
    sub_loader = get_bank_loader(sub)
    q_items = [sub_loader.questions_by_id[qid] for qid in q_ids if qid in sub_loader.questions_by_id]
    paper_item = PaperItem(
        title=title,
        paper_id=paper_id,
        subject=sub,
        mode=PaperMode.FULL_10_6_6,
        questions=q_items,
    )
    edition = PDFEdition(edition_str) if edition_str in [e.value for e in PDFEdition] else PDFEdition.REAL_EXAM
    return pdf_service.render_pdf_bytes(paper_item, edition=edition)


def _safe_pdf_bytes(*args, **kwargs):
    """渲染 PDF，失败时提示错误并返回 None（绝不交付 HTML 伪装的损坏文件）。"""
    try:
        return get_cached_pdf(*args, **kwargs)
    except Exception as ex:
        st.error(f"❌ PDF 生成失败：{ex}\n请确认系统已安装 Microsoft Edge 或 Chrome 后重试。")
        return None

# =========================================================================
# 每日错题 PDF 异步后台生成：点按钮秒回，PDF 生成完自动出现下载按钮
# =========================================================================
_EB_PDF_DIR = Path(__file__).resolve().parent / "user_data" / ".eb_pdf_cache"
try:
    _EB_PDF_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_eb_pdf_threads: dict = {}  # (sig, edition) -> thread，进程级防重复启动
_eb_pdf_threads_lock = threading.Lock()  # 保护 _eb_pdf_threads 的并发读写


def _eb_pdf_file(sig: str, edition: str) -> Path:
    return _EB_PDF_DIR / f"{sig}_{edition}.pdf"


def _eb_pdf_err_file(sig: str, edition: str) -> Path:
    return _EB_PDF_DIR / f"{sig}_{edition}.err"


def _gen_eb_pdf_worker(sig, q_items, paper_id, title, subject_str, edition_str) -> None:
    """后台线程：纯函数渲染 PDF 写文件（不碰 st / session_state / cache_data）。
    成功后清理同 edition 其它 sig 的旧文件；失败写 .err 文件。"""
    try:
        ed = PDFEdition(edition_str)
        sub = SubjectType(subject_str) if subject_str in [x.value for x in SubjectType] else SubjectType.MATH_1
        paper_item = PaperItem(title=title, paper_id=paper_id, subject=sub,
                               mode=PaperMode.FULL_10_6_6, questions=list(q_items))
        pdf = PDFService().render_pdf_bytes(paper_item, edition=ed)
        target = _eb_pdf_file(sig, edition_str)
        tmp = target.with_suffix(".pdf.tmp")
        tmp.write_bytes(pdf)
        tmp.replace(target)
        for f in _EB_PDF_DIR.glob(f"*_{edition_str}.pdf"):
            if f.name != target.name:
                # 竞态保护：存在同名 .tmp 说明该 sig 正在生成，跳过删除
                if f.with_suffix(".pdf.tmp").exists():
                    continue
                try:
                    f.unlink()
                except Exception:
                    pass
        err = _eb_pdf_err_file(sig, edition_str)
        if err.exists():
            try:
                err.unlink()
            except Exception:
                pass
    except Exception as ex:
        try:
            _eb_pdf_err_file(sig, edition_str).write_text(str(ex), encoding="utf-8")
        except Exception as log_ex:
            print(f"[EB_PDF] 生成失败且无法写错误日志 {sig}/{edition_str}: {log_ex}", file=sys.stderr)


def _ensure_eb_pdf(sig, q_items, paper_id, title, subject_str, edition_str) -> bool:
    """确保后台生成线程已启动；返回 True 表示已就绪（文件存在）。"""
    if _eb_pdf_file(sig, edition_str).exists():
        return True
    key = (sig, edition_str)
    with _eb_pdf_threads_lock:
        th = _eb_pdf_threads.get(key)
        if th is not None and not th.is_alive():
            _eb_pdf_threads.pop(key, None)  # 清理已结束线程，防字典无限增长
            th = None
        if th is None:
            th = threading.Thread(
                target=_gen_eb_pdf_worker,
                args=(sig, list(q_items), paper_id, title, subject_str, edition_str),
                daemon=True,
            )
            _eb_pdf_threads[key] = th
            th.start()
    return False


@st.fragment(run_every=5.0)
def render_eb_pdf_panel(sig: str, qs, paper_id: str, subject_str: str,
                        user_api_key: str, missing_qs, ai_ready_key: str) -> None:
    """每日错题 PDF 面板：A4 做题本常驻一键下载；详细解析版（AI 补全后）同样异步生成。
    文件就绪 → 下载按钮；生成中 → 提示可先做题；失败 → 错误 + 重试。"""
    title = f"今日错题复习 · {subject_str} · 共 {len(qs)} 题"
    wb_ed = PDFEdition.WORKBOOK_A4.value
    sol_ed = PDFEdition.SOLUTION.value
    q_items = list(qs)

    # ① A4 做题本
    if _ensure_eb_pdf(sig, q_items, paper_id, title, subject_str, wb_ed):
        st.download_button(
            "📝 下载 A4 做题本", data=_eb_pdf_file(sig, wb_ed).read_bytes(),
            file_name=f"{paper_id}_A4做题本.pdf", mime="application/pdf",
            use_container_width=True, key=f"eb_down_wb_{sig}",
        )
    elif _eb_pdf_err_file(sig, wb_ed).exists():
        _msg = _eb_pdf_err_file(sig, wb_ed).read_text(encoding="utf-8")[:200]
        st.error(f"A4 做题本生成失败：{_msg}")
        if st.button("🔄 重试生成 A4 做题本", use_container_width=True, key=f"eb_retry_wb_{sig}"):
            try:
                _eb_pdf_err_file(sig, wb_ed).unlink()
            except Exception:
                pass
            _eb_pdf_threads.pop((sig, wb_ed), None)
            st.rerun(scope="fragment")
    else:
        st.caption("⏳ A4 做题本生成中…（可先做题，生成完自动出现下载按钮）")

    # ② 详细解析版：缺解析且未补全 → 面板内触发 AI 补全；否则异步生成 PDF
    ai_ready = st.session_state.get(ai_ready_key)
    if missing_qs and not ai_ready:
        if user_api_key:
            if st.button(
                "📑 下载详细解析版（AI 补全解析）", use_container_width=True,
                key=f"eb_sol_ai_{sig}",
                help="点击后 AI 名师补齐缺失答案解析（有缓存直接复用），完成后自动出下载按钮。",
            ):
                _bar_eb = st.progress(0.0, text=f"AI 名师正在生成 {len(missing_qs)} 道题的答案解析...")
                def _cb_eb(done, total, qid, status):
                    if status == "generated":
                        _bar_eb.progress(done / total, text=f"AI 名师正在生成答案解析 ({done}/{total})：{qid}")
                    elif status == "cached":
                        _bar_eb.progress(done / total, text=f"复用已有 AI 解析缓存 ({done}/{total})...")
                    elif isinstance(status, str) and status.startswith("pdf_detail:"):
                        _bar_eb.progress(done / total, text=f"AI 解题失败，正在扫描解析 PDF 定位答案 ({done}/{total})：{qid}（{status[12:]}）")
                    elif status == "pdf":
                        _bar_eb.progress(done / total, text=f"AI 解题失败，正在扫描解析 PDF 定位答案 ({done}/{total})：{qid}")
                    elif status == "reviewing":
                        _bar_eb.progress(done / total, text=f"AI 阅卷专家正在独立审核答案 ({done}/{total})：{qid}")
                    elif status == "failed":
                        _bar_eb.progress(done / total, text=f"AI 解题失败且扫描版未命中 ({done}/{total})：{qid}")
                    elif status == "review_failed":
                        _bar_eb.progress(done / total, text=f"AI 审核失败，已标记未审核 ({done}/{total})：{qid}")
                try:
                    ensure_solutions(
                        qs,
                        _build_solution_tutor(user_api_key, user_api_url, user_model_name),
                        progress_cb=_cb_eb,
                    )
                except Exception:
                    pass
                st.session_state[ai_ready_key] = True
                st.rerun(scope="fragment")
        else:
            st.info("未配置 API Key，AI 名师暂不可用。")
    else:
        if _ensure_eb_pdf(sig, q_items, paper_id, title, subject_str, sol_ed):
            st.download_button(
                "📑 下载详细解析版", data=_eb_pdf_file(sig, sol_ed).read_bytes(),
                file_name=f"{paper_id}_详细解析.pdf", mime="application/pdf",
                use_container_width=True, key=f"eb_down_sol_{sig}",
            )
        elif _eb_pdf_err_file(sig, sol_ed).exists():
            _msg = _eb_pdf_err_file(sig, sol_ed).read_text(encoding="utf-8")[:200]
            st.error(f"详细解析版生成失败：{_msg}")
            if st.button("🔄 重试生成详细解析版", use_container_width=True, key=f"eb_retry_sol_{sig}"):
                try:
                    _eb_pdf_err_file(sig, sol_ed).unlink()
                except Exception:
                    pass
                _eb_pdf_threads.pop((sig, sol_ed), None)
                st.rerun(scope="fragment")
        else:
            st.caption("⏳ 详细解析版生成中…（生成完自动出现下载按钮）")


def _build_solution_tutor(user_api_key, user_api_url, user_model_name):
    """构造 AI 名师生成器：优先使用 ai_config.json 的 solution_*（Claude 中转），
    未配置时回退为侧边栏填写的配置（DeepSeek）。"""
    try:
        cfg = json.loads((Path(__file__).resolve().parent / "user_data" / "ai_config.json").read_text(encoding="utf-8"))
        if cfg.get("solution_api_key") and cfg.get("solution_base_url"):
            return AITutor(
                api_key=cfg["solution_api_key"],
                base_url=cfg["solution_base_url"],
                model=cfg.get("solution_model") or "claude-sonnet-4-5-20250929",
            )
    except Exception:
        pass
    return AITutor(api_key=user_api_key, base_url=user_api_url, model=user_model_name)


@st.cache_data(show_spinner="⚡ 正在生成纯净 HTML 试卷流...")
def get_cached_html(paper_id: str, title: str, q_ids: tuple[str, ...], edition_str: str = "real_exam", subject_str: str = "数学一") -> str:
    sub = SubjectType(subject_str) if subject_str in [s.value for s in SubjectType] else SubjectType.MATH_1
    sub_loader = get_bank_loader(sub)
    q_items = [sub_loader.questions_by_id[qid] for qid in q_ids if qid in sub_loader.questions_by_id]
    paper_item = PaperItem(
        title=title,
        paper_id=paper_id,
        subject=sub,
        mode=PaperMode.FULL_10_6_6,
        questions=q_items,
    )
    edition = PDFEdition(edition_str) if edition_str in [e.value for e in PDFEdition] else PDFEdition.REAL_EXAM
    return pdf_service.generate_html(paper_item, edition=edition)


# =========================================================================
# 3. Modern Clean Academic CSS Design System
# =========================================================================
def inject_modern_theme():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600;700&display=swap');

        /* Global Font and Base Styling */
        html, body, [class*="css"] {
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif !important;
            color: #1e293b;
        }

        /* Top Modern Hero Card */
        .app-hero {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 20px 28px;
            margin-bottom: 24px;
            box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.05);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 20px;
        }
        .app-hero-main {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .app-hero-icon {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
            border: 1px solid #bfdbfe;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            flex-shrink: 0;
        }
        .app-hero-title {
            font-size: 21px !important;   /* !important 压过 Streamlit 默认 h1 的 44px */
            font-weight: 800;
            color: #0f172a;
            letter-spacing: -0.02em;
            margin: 0;
            padding: 0;
            line-height: 1.3;
        }
        .app-hero-desc {
            font-size: 13px;
            color: #64748b;
            margin-top: 4px;
            font-weight: 500;
        }
        .app-hero-badge {
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            color: #2563eb;
            padding: 8px 18px;
            border-radius: 9999px;
            font-size: 13px;
            font-weight: 700;
            white-space: nowrap;
            flex-shrink: 0;
            box-shadow: 0 2px 4px rgba(37, 99, 235, 0.06);
        }
        /* 手机窄屏:hero 竖向堆叠，标题独占整行不被挤成竖排 */
        @media (max-width: 640px) {
            .app-hero {
                flex-direction: column;
                align-items: flex-start;
                gap: 12px;
                padding: 16px 18px;
            }
            .app-hero-main {
                gap: 12px;
                width: 100%;
            }
            .app-hero-title {
                font-size: 18px !important;   /* 手机再收一号，确保七字一行 */
                white-space: nowrap;
            }
            .app-hero-desc {
                font-size: 12px;
            }
            .app-hero-badge {
                white-space: normal;   /* 徽章移到下方，允许自然换行 */
                align-self: flex-start;
            }
        }

        /* Modern Card Styling */
        .glass-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px -2px rgba(15, 23, 42, 0.04);
            transition: all 0.2s ease;
        }

        /* Question Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            padding: 3px 9px;
            border-radius: 6px;
            font-size: 11.5px;
            font-weight: 700;
            margin-right: 6px;
        }
        .badge-basic { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
        .badge-comp { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
        .badge-adv { background: #faf5ff; color: #7e22ce; border: 1px solid #e9d5ff; }
        .badge-ch { background: #f8fafc; color: #475569; border: 1px solid #e2e8f0; }
        .badge-tag { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
        .badge-wrong { background: #fff1f2; color: #be123c; border: 1px solid #fecdd3; }

        /* Formula & Math Typography */
        .katex, .katex-display {
            font-size: 1.06em !important;
            color: #0f172a !important;
        }

        /* Pitfall & Solution Callout */
        .pitfall-callout {
            background: #fffbeb;
            border-left: 4px solid #f59e0b;
            padding: 14px 18px;
            border-radius: 0 10px 10px 0;
            margin: 12px 0;
            font-size: 13.5px;
            color: #92400e;
            border-top: 1px solid #fef3c7;
            border-right: 1px solid #fef3c7;
            border-bottom: 1px solid #fef3c7;
        }

        /* Radar Matrix Badges */
        .radar-pill {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 12px;
            margin: 4px;
            font-weight: 600;
        }
        .radar-pill-done {
            background: #ecfdf5;
            color: #047857;
            border: 1px solid #a7f3d0;
        }
        .radar-pill-todo {
            background: #f8fafc;
            color: #64748b;
            border: 1px dashed #cbd5e1;
        }

        /* Streamlit Tab Styles */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            border-bottom: 2px solid #e2e8f0;
            margin-bottom: 24px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 46px;
            font-weight: 700;
            font-size: 14.5px;
            border-radius: 8px 8px 0 0;
            padding: 0 20px;
            color: #64748b;
        }
        .stTabs [aria-selected="true"] {
            color: #2563eb !important;
        }

        /* Metric Enhancement */
        [data-testid="stMetricValue"] {
            font-family: 'Plus Jakarta Sans', sans-serif !important;
            font-weight: 800 !important;
            color: #0f172a !important;
        }

        /* Question Card Container */
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px !important;
            border-color: #e2e8f0 !important;
            background: #ffffff !important;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03) !important;
            transition: all 0.2s ease !important;
            margin-bottom: 14px !important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover {
            border-color: #cbd5e1 !important;
            box-shadow: 0 4px 14px -2px rgba(15, 23, 42, 0.05) !important;
        }

        /* BaseWeb Select & Multiselect "No results" / "No options" localization */
        div[data-baseweb="menu"] li[aria-disabled="true"],
        div[data-baseweb="popover"] li[aria-disabled="true"],
        div[data-baseweb="menu"] div[aria-disabled="true"],
        div[data-baseweb="popover"] div[aria-disabled="true"] {
            font-size: 0 !important;
            min-height: 36px !important;
            display: flex !important;
            align-items: center !important;
        }
        div[data-baseweb="menu"] li[aria-disabled="true"]::after,
        div[data-baseweb="popover"] li[aria-disabled="true"]::after,
        div[data-baseweb="menu"] div[aria-disabled="true"]::after,
        div[data-baseweb="popover"] div[aria-disabled="true"]::after {
            content: "暂无匹配结果" !important;
            font-size: 13.5px !important;
            color: #94a3b8 !important;
            font-weight: 500 !important;
            padding: 6px 12px !important;
            display: block !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_modern_theme()


# =========================================================================
# 4. Global Sidebar Controls (Book & Subject & Wrong Book & AI)
# =========================================================================
with st.sidebar:
    st.markdown("### 📚 考研拼好卷系统")

    # 关闭服务按钮
    if st.button("🔴 关闭服务", use_container_width=True, help="点击后停止后台运行的拼卷系统，浏览器页面将断开。"):
        import os
        st.warning("正在关闭服务...")
        try:
            os._exit(0)
        except Exception:
            pass
    st.markdown("---")

    # 1. 优先选择科目（通过 URL 参数 sub 持久化，刷新不丢）
    _subject_options = ["数学一", "数学二", "数学三", "自定义"]
    _default_sub = st.query_params.get("sub", "数学一")
    _subject_idx = _subject_options.index(_default_sub) if _default_sub in _subject_options else 0
    selected_subject_str = st.selectbox(
        "🎯 考研数学科目",
        options=_subject_options,
        index=_subject_idx,
        key="selected_subject_key",
    )
    if st.query_params.get("sub") != selected_subject_str:
        st.query_params["sub"] = selected_subject_str
    if selected_subject_str == "数学一":
        current_subject = SubjectType.MATH_1
    elif selected_subject_str == "数学二":
        current_subject = SubjectType.MATH_2
    elif selected_subject_str == "数学三":
        current_subject = SubjectType.MATH_3
    else:
        current_subject = SubjectType.CUSTOM

    # 先加载题库(含 880 + 真题),再据实际书籍渲染多选
    loader = get_bank_loader(current_subject if current_subject != SubjectType.CUSTOM else SubjectType.MATH_1)
    raw_questions = list(_cached_questions((current_subject if current_subject != SubjectType.CUSTOM else SubjectType.MATH_1).value))

    # 2. 选择参考书籍 (勾选，多选汇聚题库池)。选项来自题库实际书籍,880 置顶。
    # 勾选状态编码进网址 bk1/bk2/bk3(按科目)，下次打开自动恢复上次的选择，不写死默认 880。
    _books_present = {getattr(q, "book", "880") for q in raw_questions}
    available_books = (["880"] if "880" in _books_present else []) + sorted(_books_present - {"880"})
    _BOOK_URL_KEY = {SubjectType.MATH_1: "bk1", SubjectType.MATH_2: "bk2", SubjectType.MATH_3: "bk3"}
    _book_url_key = _BOOK_URL_KEY.get(
        current_subject if current_subject != SubjectType.CUSTOM else SubjectType.MATH_1
    )
    # 书籍↔短码:与位图参数同风格(8=880, z=真题, t=1000题);未知书名回退到名字首字符
    _BOOK_CODE = {"880": "8", "真题2010-2026": "z", "张宇1000题": "t"}

    def _book_to_code(name: str) -> str:
        return _BOOK_CODE.get(name) or (name[:1] if name else "?")

    _code_to_book = {_book_to_code(b): b for b in available_books}

    # 首次渲染本科目时,用网址里的勾选状态给各 checkbox 播种(之后由用户勾选主导)
    _bk_seed_flag = f"_bk_seeded_{current_subject.value}"
    if _bk_seed_flag not in st.session_state:
        st.session_state[_bk_seed_flag] = True
        _incoming_bk = st.query_params.get(_book_url_key) if _book_url_key else None
        if _incoming_bk is not None:
            # 网址有记录 → 严格按它恢复(空串 = 上次一本都没勾,也如实恢复)
            _restored = {_code_to_book[c] for c in _incoming_bk if c in _code_to_book}
            for b in available_books:
                st.session_state[f"bk_cb_{current_subject.value}_{b}"] = (b in _restored)
        else:
            # 无记录(全新访客)→ 全部勾上,让人一眼看到题库全貌
            for b in available_books:
                st.session_state[f"bk_cb_{current_subject.value}_{b}"] = True

    st.markdown("📚 **选择参考书籍**")
    # 侧栏宽度有限,竖排一行一本 —— 分列会把书名截断成「张宇1…」
    selected_books = []
    for _bk in available_books:
        if st.checkbox(_bk, key=f"bk_cb_{current_subject.value}_{_bk}"):
            selected_books.append(_bk)
    if not selected_books:
        st.caption("⚠️ 未勾选任何书籍，组卷与题库页将无题可用。")

    # 勾选状态写回网址(顺序按 available_books,保证同一组合编码稳定)
    if _book_url_key:
        _bk_code = "".join(_book_to_code(b) for b in available_books if b in selected_books)
        if st.query_params.get(_book_url_key) != _bk_code:
            st.query_params[_book_url_key] = _bk_code

    current_books_str = "、".join(selected_books) if selected_books else "未选择书籍"

    all_questions = [q for q in raw_questions if getattr(q, "book", "880") in selected_books]

    # 动态提炼章节结构，彻底避免写死任何固定参考书的章节划分与数量
    loaded_chapters = []
    seen_ch = set()
    for q in all_questions:
        if q.chapter and q.chapter not in seen_ch:
            seen_ch.add(q.chapter)
            loaded_chapters.append(q.chapter)
    default_target_chapters = set(loaded_chapters)

    # 科目变更监听与右侧试卷状态自动重置
    if "last_selected_subject" not in st.session_state:
        st.session_state.last_selected_subject = selected_subject_str
    elif st.session_state.last_selected_subject != selected_subject_str:
        st.session_state.last_selected_subject = selected_subject_str
        st.session_state.current_paper_p1 = None
        st.session_state.current_bundle_p1 = None

    # 根据当前科目自动挂载专属错题本（数一/数二/数三相互独立隔离）
    active_sub = current_subject if current_subject != SubjectType.CUSTOM else SubjectType.MATH_1
    state_mgr = StateManager(username=current_user_param, subject=active_sub)

    # URL 位图：每科目一个查询参数键，可在同一网址里并存三科错题状态
    URL_SUBJECT_KEY = {
        SubjectType.MATH_1: "d1",
        SubjectType.MATH_2: "d2",
        SubjectType.MATH_3: "d3",
    }
    url_data_key = URL_SUBJECT_KEY.get(active_sub)
    # seen 位图参数键(已抽过的题),与错题位图 d1/d2/d3 独立并存
    URL_SEEN_KEY = {SubjectType.MATH_1: "n1", SubjectType.MATH_2: "n2", SubjectType.MATH_3: "n3"}
    url_seen_key = URL_SEEN_KEY.get(active_sub)
    # 位图 canonical 列表锁定到具体书籍(880),独立于科目里是否还有别的书。
    # 880 的题号列表/顺序/签名恒定 → 880 旧 URL 永不失效(真题接入不影响)。
    URL_BITMAP_BOOK = "880"
    canonical_ids = loader.canonical_ids(book=URL_BITMAP_BOOK)
    # 真题(第二本书)独立位图参数,锚定真题自己的 canonical,与 880 的 d/n 完全隔离。
    ZHENTI_BOOK = "真题2010-2026"
    URL_ZHENTI_KEY = {SubjectType.MATH_1: "z1", SubjectType.MATH_2: "z2", SubjectType.MATH_3: "z3"}
    URL_ZHENTI_SEEN_KEY = {SubjectType.MATH_1: "zn1", SubjectType.MATH_2: "zn2", SubjectType.MATH_3: "zn3"}
    url_zhenti_key = URL_ZHENTI_KEY.get(active_sub)
    url_zhenti_seen_key = URL_ZHENTI_SEEN_KEY.get(active_sub)
    zhenti_canonical = loader.canonical_ids(book=ZHENTI_BOOK)
    # 张宇1000题(第三本书)独立位图参数 t/tn,锚定自己的 canonical,与 880 的 d/n、真题的 z/zn 全隔离。
    # 新参数名 → 旧链接没有 t/tn 即空集,880/真题 签名与恢复逻辑完全不受影响。
    BOOK_1000 = "张宇1000题"
    URL_1000_KEY = {SubjectType.MATH_1: "t1", SubjectType.MATH_2: "t2", SubjectType.MATH_3: "t3"}
    URL_1000_SEEN_KEY = {SubjectType.MATH_1: "tn1", SubjectType.MATH_2: "tn2", SubjectType.MATH_3: "tn3"}
    url_1000_key = URL_1000_KEY.get(active_sub)
    url_1000_seen_key = URL_1000_SEEN_KEY.get(active_sub)
    canonical_1000 = loader.canonical_ids(book=BOOK_1000)

    # 首次进入本科目且网址带错题码时，从 URL 恢复（无后端跨设备恢复）
    if url_data_key and current_subject != SubjectType.CUSTOM:
        restore_flag = f"_url_restored_{active_sub.value}"
        if restore_flag not in st.session_state:
            st.session_state[restore_flag] = True
            incoming_code = st.query_params.get(url_data_key)
            if incoming_code:
                n_restored, restore_status = state_mgr.apply_url_code(incoming_code, canonical_ids)
                if restore_status == "stale":
                    st.session_state["_url_restore_stale"] = active_sub.value
            # 真题错题码:merge=True 合并,不覆盖上面恢复的 880 错题(题号不撞)
            incoming_zt = st.query_params.get(url_zhenti_key) if url_zhenti_key else None
            if incoming_zt and zhenti_canonical:
                state_mgr.apply_url_code(incoming_zt, zhenti_canonical, merge=True)
            # 恢复"已抽过题"(seen)——与错题码独立,合并进本地累积集合
            incoming_seen = st.query_params.get(url_seen_key) if url_seen_key else None
            if incoming_seen:
                state_mgr.apply_seen_url_code(incoming_seen, canonical_ids)
            # 真题 seen(apply_seen 本就是并集合并,天然安全)
            incoming_zt_seen = st.query_params.get(url_zhenti_seen_key) if url_zhenti_seen_key else None
            if incoming_zt_seen and zhenti_canonical:
                state_mgr.apply_seen_url_code(incoming_zt_seen, zhenti_canonical)
            # 1000题错题码:同真题,merge=True 合并(题号与 880/真题 不撞)
            incoming_1k = st.query_params.get(url_1000_key) if url_1000_key else None
            if incoming_1k and canonical_1000:
                state_mgr.apply_url_code(incoming_1k, canonical_1000, merge=True)
            # 1000题 seen
            incoming_1k_seen = st.query_params.get(url_1000_seen_key) if url_1000_seen_key else None
            if incoming_1k_seen and canonical_1000:
                state_mgr.apply_seen_url_code(incoming_1k_seen, canonical_1000)

    # URL 试卷码：记住上次生成的是哪几道题（不含组卷配置），做完后跨设备查阅答案。
    # 每科目一个参数键 q1/q2/q3。当右侧无当前试卷时（首次进入 / 切科目回来）从 URL 恢复。
    PAPER_URL_KEY = {SubjectType.MATH_1: "q1", SubjectType.MATH_2: "q2", SubjectType.MATH_3: "q3"}
    paper_url_key = PAPER_URL_KEY.get(active_sub)
    if paper_url_key and current_subject != SubjectType.CUSTOM and not st.session_state.get("current_paper_p1"):
        # 优先从 URL 的 q 码恢复（跨设备）；无 q 码时回退到本地文件里"上次生成的卷"（本地重开）
        papers_qids: list[list[str]] = []
        pcode = st.query_params.get(paper_url_key)
        if pcode:
            decoded, pstatus = StateManager.decode_papers_code(pcode, canonical_ids)
            if pstatus == "ok" and decoded:
                papers_qids = decoded
        if not papers_qids and state_mgr.last_papers_qids:
            # 本地存档；过滤当前题库仍存在的题号，避免题库变动后错位
            valid = set(canonical_ids)
            papers_qids = [[q for q in p if q in valid] for p in state_mgr.last_papers_qids]
            papers_qids = [p for p in papers_qids if p]
        if papers_qids:
            def _rebuild_paper(qids: list[str], idx: int, total: int) -> PaperItem:
                qs = [loader.questions_by_id[q] for q in qids if q in loader.questions_by_id]
                label = f"（{chr(65 + idx)}卷）" if total > 1 else ""
                return PaperItem(
                    title=f"上次生成的{active_sub.value}试卷{label}",
                    paper_id=f"RESTORED-{active_sub.value}-{idx}",
                    subject=active_sub,
                    mode=PaperMode.CUSTOM,
                    questions=qs,
                )
            built = [_rebuild_paper(q, i, len(papers_qids)) for i, q in enumerate(papers_qids) if q]
            if len(built) == 1:
                st.session_state.current_paper_p1 = built[0]
                st.session_state.current_bundle_p1 = None
            elif len(built) > 1:
                st.session_state.current_bundle_p1 = PaperBundle(
                    bundle_id=f"RESTORED-{active_sub.value}",
                    title=f"上次生成的{active_sub.value}联考套卷",
                    subject=active_sub,
                    papers=built,
                )
                st.session_state.current_paper_p1 = built[0]

    # 当前科目的活跃错题池与历史错题池计算
    active_wrong_ids = state_mgr.get_active_wrong_question_ids()
    all_wrong_ids = state_mgr.get_all_wrong_question_ids()
    subject_active_wrong_pool = [q for q in all_questions if q.id in active_wrong_ids]
    subject_wrong_pool = subject_active_wrong_pool
    subject_wrong_count = len(subject_active_wrong_pool)
    past_wrong_count = len([q for q in all_questions if q.id in all_wrong_ids and not state_mgr.is_in_active_pool(q.id)])
    repeated_wrong_count = sum(1 for q in subject_active_wrong_pool if state_mgr.get_wrong_count(q.id) >= 2)

    # 极速响应回调函数 (基于 on_click 单程更新，彻底消除双重刷新卡顿)
    def cb_toggle_wrong(qid: str):
        state_mgr.toggle_wrong_question(qid)

    def cb_remove_wrong(qid: str):
        state_mgr.remove_wrong_question(qid)

    def cb_archive_to_history(qid: str):
        state_mgr.mark_solved_correctly(qid)

    def cb_reactivate_wrong(qid: str):
        state_mgr.reactivate_to_pool(qid)

    def cb_inc_wrong(qid: str, delta: int = 1):
        state_mgr.increment_wrong_count(qid, delta=delta)

    st.markdown("---")
    st.markdown("### 📕 错题本画像")
    sb_c1, sb_c2 = st.columns(2)
    with sb_c1:
        st.metric(
            label="🎯 待练错题",
            value=f"{subject_wrong_count} 题",
        )
    with sb_c2:
        st.metric(
            label="🏆 历史错题",
            value=f"{past_wrong_count} 题",
        )

    st.metric(
        label="🔥 顽固错题",
        value=f"{repeated_wrong_count} 题",
    )

    _due_today = state_mgr.get_due_wrong_count()
    st.metric(
        label="⏰ 今日到期（艾宾浩斯）",
        value=f"{_due_today} 题",
        help="按 1/2/4/7/15/30 天抗遗忘曲线，今天该复习的错题数。",
    )

    if subject_wrong_count > 0 or past_wrong_count > 0:
        if st.button("🗑️ 清空所有错题记录", use_container_width=True):
            state_mgr.clear_all_wrong()
            st.success("已清空错题记录！")
            st.rerun()

    if st.session_state.get("_url_restore_stale") == active_sub.value:
        st.warning("⚠️ 网址中的错题码与当前题库版本不匹配（题库已更新），未自动恢复，以本地记录为准。")

    if IS_CLOUD:
        st.markdown(
            "⚠️ **必须保存本页链接！** 你的错题记录**与上次生成的试卷**都已编码进当前网址（连同 AI 服务商 / 模型）。"
            "本服务运行在云端，**不保存你的数据**——一旦关闭页面又没存下这条链接，"
            "**错题记录和试卷将永久丢失且无法找回**。请立刻【收藏本页】或复制网址存好，换设备时打开它即可恢复。"
            "（API Key 出于安全不写入网址。）另建议定期用下方【导出错题本备份】再存一份 JSON。"
        )
    else:
        st.caption(
            "🔗 错题记录**与上次生成的试卷**已自动保存在本机文件，重开本页即在，无需记链接。"
            "该网址也编码了你的错题、试卷与 AI 配置，复制它可在其他设备恢复（API Key 不写入网址）。"
        )

    st.markdown("---")
    st.markdown("### 🤖 AI 名师答疑")

    preset_providers = {
        "DeepSeek": ("https://api.deepseek.com", "deepseek-chat"),
        "DeepSeek-R1": ("https://api.deepseek.com", "deepseek-reasoner"),
        "SiliconFlow": ("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
        "OpenAI": ("https://api.openai.com/v1", "gpt-4o"),
        "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        "智谱清言": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
        "Ollama 本地": ("http://localhost:11434/v1", "llama3"),
        "自定义": ("", ""),
    }

    # 首次进入时用网址里保存的 Base URL / Model 作为初值（Key 永不进 URL）
    if "_ai_cfg_seeded" not in st.session_state:
        st.session_state["_ai_cfg_seeded"] = True
        st.session_state.setdefault("ai_base_url", st.query_params.get("au") or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"))
        st.session_state.setdefault("ai_model", st.query_params.get("am") or "deepseek-chat")

    # 切换预设服务商时，自动带出其 Base URL 与 Model（自定义不覆盖已填内容）
    def _apply_provider_preset():
        url, model = preset_providers.get(st.session_state.get("ai_provider"), ("", ""))
        if url:
            st.session_state["ai_base_url"] = url
        if model:
            st.session_state["ai_model"] = model

    _provider_options = list(preset_providers.keys())
    _default_provider = st.query_params.get("ap", "DeepSeek")
    _provider_idx = _provider_options.index(_default_provider) if _default_provider in _provider_options else 0
    selected_provider = st.selectbox(
        "服务提供商",
        options=_provider_options,
        index=_provider_idx,
        key="ai_provider",
        on_change=_apply_provider_preset,
        help="选择预设提供商或选择自定义以连接任意兼容 OpenAI 规范的大模型 API。",
    )
    if st.query_params.get("ap") != selected_provider:
        st.query_params["ap"] = selected_provider

    user_api_url = st.text_input(
        "API Base URL",
        key="ai_base_url",
        help="大模型 API Base URL（例如 https://api.deepseek.com 或 https://api.openai.com/v1）。",
    )
    # 首次进入时从本地文件恢复 API Key（不写入 URL，仅存本机）
    if "_api_key_seeded" not in st.session_state:
        st.session_state["_api_key_seeded"] = True
        _saved_cfg = load_ai_config()
        _saved_key = _saved_cfg.get("api_key", "")
        _env_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        st.session_state["ai_api_key"] = _saved_key or _env_key

    user_api_key = st.text_input(
        "API Key",
        key="ai_api_key",
        type="password",
        help="API Key 保存在本机 user_data/ai_config.json，刷新页面不丢失；出于安全不写入网址。",
    )
    # API Key 变化时自动保存到本地文件
    if user_api_key:
        _cfg = load_ai_config()
        if _cfg.get("api_key", "") != user_api_key:
            _cfg["api_key"] = user_api_key
            save_ai_config(_cfg)
    user_model_name = st.text_input(
        "Model 模型名称",
        key="ai_model",
        help="调用的模型名称（例如 deepseek-chat, gpt-4o, qwen-plus 等）。",
    )

    # ===== API Key 健康检查（缓存加固配套：进入页面惰性探测，TTL 5 分钟防重复打；并行探测两个配置）=====
    # 只查在用配置：生成（solution_* Claude 中转）与审核（review_* DeepSeek）；双模型未配置时兜底查侧边栏 Key。
    # 结果存 session_state（跨 rerun 保留）+ ai_health 内部 TTL 缓存（跨调用去重），"重新检测"按钮 force 重探。
    with st.container():
        st.markdown("#### 🔑 API 状态")
        _health_cfg = load_ai_config()
        _health_targets = []
        if _health_cfg.get("solution_api_key") and _health_cfg.get("solution_base_url"):
            _health_targets.append(("生成模型 Claude", _health_cfg["solution_api_key"],
                                    _health_cfg["solution_base_url"],
                                    _health_cfg.get("solution_model") or "claude-sonnet-4-5-20250929"))
        if _health_cfg.get("review_api_key") and _health_cfg.get("review_base_url"):
            _health_targets.append(("审核模型 DeepSeek", _health_cfg["review_api_key"],
                                    _health_cfg["review_base_url"],
                                    _health_cfg.get("review_model") or "deepseek-chat"))
        if not _health_targets and user_api_key:
            _health_targets.append(("侧边栏 Key", user_api_key, user_api_url, user_model_name))

        # ===== 今日 AI 消耗（本地成本日志，0 API 开销）=====
        try:
            from pathlib import Path
            import datetime as _dt
            import json as _json
            _log_p = Path(__file__).resolve().parent / "user_data" / "ai_cost_log.json"
            if _log_p.exists():
                _log = _json.loads(_log_p.read_text(encoding="utf-8"))
                _today = _dt.date.today().strftime("%Y-%m-%d")
                _t = [e for e in _log if (e.get("ts") or "").startswith(_today)]
                if _t:
                    _req = len(_t)
                    _pt = sum(int(e.get("pt") or 0) for e in _t)
                    _ct = sum(int(e.get("ct") or 0) for e in _t)
                    _fail = sum(1 for e in _t if not e.get("ok"))
                    st.markdown(
                        f"#### 📊 今日 AI 消耗\\n"
                        f"`{_req}` 次请求 · 输入 `{_pt:,}` / 输出 `{_ct:,}` tokens"
                        + (f" · ⚠️ `{_fail}` 次失败" if _fail else "")
                    )
        except Exception:
            pass
        if not _health_targets:
            st.caption("未配置 API Key，AI 解析功能不可用。")
        else:
            # 后台探测：主脚本 0 等待（结果由 fragment 轮询自动显示，不阻塞任何 rerun；
            # key/base/model 变化时自动重探）
            for _label, _k, _b, _m in _health_targets:
                _skey = f"_health_{_label}"
                _ent = _bg_health.get(_skey)
                if _ent is None or _ent.get("fp") != _health_fp(_k, _b, _m):
                    # key/base/model 变化 → force 重探（绕过 ai_health TTL 缓存，防止旧指纹误命中）
                    _start_bg_probe(_skey, _k, _b, _m, force=(_ent is not None))
            render_health_panel(_health_targets)
        st.markdown("---")


# =========================================================================
# 5. Top Modern Hero Header
# =========================================================================
st.markdown(
    f"""
    <div class="app-hero">
        <div class="app-hero-main">
            <div class="app-hero-icon">📚</div>
            <div>
                <h1 class="app-hero-title">考研拼好卷系统</h1>
                <div class="app-hero-desc">跨题库精准拼卷 · 经典书目穿透 · 错题靶向攻坚 · 智能自适应组卷</div>
            </div>
        </div>
        <div class="app-hero-badge">
            📚 {current_books_str} · 🔥 {current_subject.value} · {len(loaded_chapters)} 章 · {len(all_questions)} 题
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================================
# 6. Three Core Workspaces (Tabs)
# =========================================================================
# 惰性模块导航（替代 st.tabs）：st.tabs 每次全量 rerun 会执行全部 5 个 tab 内容，
# 导致刷新页面要渲染所有模块而卡顿。segmented_control + if/elif 只渲染激活模块，
# 其余模块点开才渲染 —— 首屏成本降到约 1/5。
st.session_state.setdefault("active_ws", "🎯 智能拼好卷")
active_module = st.segmented_control(
    "模块导航",
    options=["🎯 智能拼好卷", "🏷️ 题库逐题标错", "📕 我的错题本", "📈 全科考点雷达", "📅 每日错题"],
    key="active_ws",
    label_visibility="collapsed",
) or "🎯 智能拼好卷"  # 点击已选中项可能返回 None，兜底回默认模块，防页面空白


# -------------------------------------------------------------------------
# WORKSPACE 1: 智能拼好卷大厅 (核心组卷与刷题，默认以错题组卷)
# -------------------------------------------------------------------------
if active_module == "🎯 智能拼好卷":
    # 从本地文件恢复拼卷配置：每次渲染都 setdefault 注入文件值。
    # setdefault 只在 key 缺失时写入，不覆盖用户本次已调整的值；
    # 修复"仅 seed 一次"导致会话异常后配置永久停留在代码默认值的问题。
    _saved_paper_cfg = load_paper_config()
    for k, v in _saved_paper_cfg.items():
        st.session_state.setdefault(k, v)
    # "展开全部解析"常开:每次进入拼卷页强制展开(用户要求默认展示完整解析,不保留上次收起状态)
    st.session_state["p1_show_ans_cb"] = True

    st.markdown("#### 🎯 智能拼卷配置")

    # 1. 核心题源配置：错题占比滑块（错题 : 新题 混合，错题不足自动用新题补齐）
    src_col1, src_col2 = st.columns([2, 1])
    with src_col1:
        default_ratio = 70 if subject_wrong_count > 0 else 0
        wrong_ratio_pct = st.slider(
            "🎯 错题占比（其余用新题补足；错题不够时自动用新题补齐）",
            min_value=0, max_value=100, value=default_ratio, step=10,
            key=f"p1_wrong_ratio_{current_subject.value}",
            help="0% = 全书模考（全新题）；100% = 尽量全用错题，不足部分用新题补齐；中间值按比例混合。",
        )
        wrong_ratio = wrong_ratio_pct / 100.0
    with src_col2:
        if wrong_ratio_pct > 0 and subject_wrong_count == 0:
            st.warning("⚠️ 当前错题池暂无题目，将全部用新题组卷。请前往【逐题标错】录入错题。")
        else:
            # 按书分别统计:每本书 总题数 · 已标错题数
            _wrong_ids_now = {q.id for q in subject_active_wrong_pool}
            _lines = []
            for _bk in selected_books:
                _tot = sum(1 for q in all_questions if getattr(q, "book", "880") == _bk)
                _wr = sum(1 for q in all_questions if getattr(q, "book", "880") == _bk and q.id in _wrong_ids_now)
                _lines.append(f"《{_bk}》{_tot} 题 · 已标错 {_wr} 题")
            st.caption(" ｜ ".join(_lines) if _lines else "未选择书籍")
        exclude_seen = st.checkbox(
            "🚫 避免重复抽题（抽过的新题不再抽）",
            value=True,
            key=f"p1_exclude_seen_{current_subject.value}",
            help="开启后组卷会跳过你之前抽到过的新题；抽完全书后自动重新允许。错题重练不受影响。",
        )
        _dist_all = get_chapter_dist()
        _has_dist = current_subject.value in _dist_all
        use_real_dist = st.checkbox(
            "📊 参考真题章节分布",
            value=_has_dist,
            disabled=not _has_dist,
            key=f"p1_use_dist_{current_subject.value}",
            help="按 2010-2026 真题各章考频加权:真题里考得多的章,组卷更易抽中(软偏好,保留随机性)。",
        ) if _has_dist else False
        real_dist_strength = 0.0
        if use_real_dist:
            real_dist_strength = st.slider(
                "真题分布强度", 0.0, 1.0, 0.6, 0.1,
                key=f"p1_dist_strength_{current_subject.value}",
                help="0=不参考(与关闭同) ~ 1=完全按真题考频。默认0.6适度偏向,避免过拟合。",
            )

    # 2. 规格与难度配置
    cfg_col1, cfg_col2, cfg_col3 = st.columns([1.5, 1.5, 1.5])
    with cfg_col1:
        mode_choice = st.radio(
            "拼卷规格",
            options=[
                PaperMode.FULL_10_6_6.value,
                PaperMode.SPRINT_5_3_3.value,
                PaperMode.BUNDLE_3_PAPERS.value,
                PaperMode.CUSTOM.value,
            ],
            index=0,
            key=f"p1_mode_{current_subject.value}",
        )
        current_mode = PaperMode(mode_choice)

        st.markdown("##### 按照真题大纲")
        if current_subject == SubjectType.MATH_2:
            cat_c1, cat_c2 = st.columns(2)
            with cat_c1:
                chk_math = st.checkbox("高等数学", value=True, key=f"p1_chk_math_{current_subject.value}")
            with cat_c2:
                chk_linalg = st.checkbox("线性代数", value=True, key=f"p1_chk_linalg_{current_subject.value}")
            chk_prob = False
        else:
            cat_c1, cat_c2, cat_c3 = st.columns(3)
            with cat_c1:
                chk_math = st.checkbox("高等数学", value=True, key=f"p1_chk_math_{current_subject.value}")
            with cat_c2:
                chk_linalg = st.checkbox("线性代数", value=True, key=f"p1_chk_linalg_{current_subject.value}")
            with cat_c3:
                chk_prob = st.checkbox("概率论", value=True, key=f"p1_chk_prob_{current_subject.value}")

        enabled_categories = set()
        if chk_math: enabled_categories.add(ChapterCategory.ADVANCED_MATH)
        if chk_linalg: enabled_categories.add(ChapterCategory.LINEAR_ALGEBRA)
        if chk_prob: enabled_categories.add(ChapterCategory.PROBABILITY)
        if not enabled_categories:
            enabled_categories = {ChapterCategory.ADVANCED_MATH, ChapterCategory.LINEAR_ALGEBRA} if current_subject == SubjectType.MATH_2 else {ChapterCategory.ADVANCED_MATH, ChapterCategory.LINEAR_ALGEBRA, ChapterCategory.PROBABILITY}

    with cfg_col2:
        st.markdown("##### ⚖️ 难度梯度倾向")
        w_basic = st.slider("基础题倾向", 0.0, 2.0, 1.0, 0.1, key="p1_basic")
        w_comp = st.slider("综合题倾向", 0.0, 2.0, 1.2, 0.1, key="p1_comp")
        w_adv = st.slider("拓展题倾向", 0.0, 2.0, 0.8, 0.1, key="p1_adv")
        diff_weights = {
            DifficultyLevel.BASIC: w_basic,
            DifficultyLevel.COMPREHENSIVE: w_comp,
            DifficultyLevel.ADVANCED: w_adv,
        }

    with cfg_col3:
        st.markdown("##### 🎯 专项考点标签")
        selected_tag = st.selectbox(
            "考点标签",
            options=["全部", "高频经典", "易错点", "计算量大", "综合压轴"],
            index=0,
            key="p1_tag",
        )
        custom_counts = {}
        custom_category_weights = {}
        if current_mode == PaperMode.CUSTOM:
            st.markdown("##### 自由题量配置")
            cc1, cc2, cc3 = st.columns(3)
            with cc1: custom_counts[QuestionType.CHOICE] = st.number_input("选择题数量", 0, 30, 10, key="custom_qc")
            with cc2: custom_counts[QuestionType.FILL_BLANK] = st.number_input("填空题数量", 0, 20, 6, key="custom_qf")
            with cc3: custom_counts[QuestionType.SOLUTION] = st.number_input("解答题数量", 0, 15, 6, key="custom_qs")

            st.markdown("##### 学科比例配置")
            if current_subject == SubjectType.MATH_2:
                rc1, rc2 = st.columns(2)
                with rc1: w_math = st.number_input("高等数学比例", min_value=0, max_value=100, value=78, step=1, key="c_w_math")
                with rc2: w_linalg = st.number_input("线性代数比例", min_value=0, max_value=100, value=22, step=1, key="c_w_linalg")
                custom_category_weights = {
                    ChapterCategory.ADVANCED_MATH: float(w_math),
                    ChapterCategory.LINEAR_ALGEBRA: float(w_linalg),
                }
            else:
                rc1, rc2, rc3 = st.columns(3)
                with rc1: w_math = st.number_input("高等数学比例", min_value=0, max_value=100, value=56, step=1, key="c_w_math")
                with rc2: w_linalg = st.number_input("线性代数比例", min_value=0, max_value=100, value=22, step=1, key="c_w_linalg")
                with rc3: w_prob = st.number_input("概率论比例", min_value=0, max_value=100, value=22, step=1, key="c_w_prob")
                custom_category_weights = {
                    ChapterCategory.ADVANCED_MATH: float(w_math),
                    ChapterCategory.LINEAR_ALGEBRA: float(w_linalg),
                    ChapterCategory.PROBABILITY: float(w_prob),
                }

    # 自动保存当前拼卷配置到本地文件（每次 rerun 都存，用户改了什么都能记住）
    save_paper_config()

    # 3. 组卷触发按钮
    st.markdown("---")
    act_col1, act_col2 = st.columns([3, 1])
    with act_col1:
        button_title = f"🚀 立即生成 {current_subject.value} 试卷"
        start_assemble = st.button(button_title, type="primary", use_container_width=True, key=f"p1_gen_btn_{current_subject.value}")
    with act_col2:
        if st.button("🔄 重置覆盖轮次", use_container_width=True, key="p1_reset_cycle"):
            state_mgr.reset_coverage_cycle()
            st.success("已重置覆盖轮次！")
            st.rerun()

    # 整套真题卷:选年份 → 直接出该年完整原卷(绕过组卷引擎,按题号原序)
    _zhenti_qs = [q for q in raw_questions if getattr(q, "year", "")]
    if _zhenti_qs:
        with st.expander("📅 或:直接做整套历年真题卷", expanded=False):
            _years = sorted({q.year for q in _zhenti_qs})  # 2010 在最前
            zy1, zy2 = st.columns([2, 1])
            with zy1:
                pick_year = st.selectbox("选择年份", _years, index=0, key=f"p1_zhenti_year_{current_subject.value}")
            with zy2:
                gen_zhenti = st.button("📄 出这套真题", use_container_width=True, key=f"p1_zhenti_gen_{current_subject.value}")
            st.caption("整套真题按原卷题号顺序呈现(暂无答案解析,可用 AI 名师答疑)。")
            if gen_zhenti:
                year_qs = sorted([q for q in _zhenti_qs if q.year == pick_year], key=lambda q: parse_qid_tuple(q.id))
                paper = PaperItem(
                    title=f"{pick_year}年 {current_subject.value} 考研真题(原卷)",
                    paper_id=f"真题-{current_subject.value}-{pick_year}",
                    subject=current_subject,
                    mode=PaperMode.FULL_10_6_6,
                    questions=year_qs,
                    target_chapters={q.chapter for q in year_qs},
                )
                st.session_state.current_paper_p1 = paper
                st.session_state.current_bundle_p1 = None
                state_mgr.record_paper_generation(year_qs)
                state_mgr.set_last_papers([[q.id for q in year_qs]])
                st.rerun()

    # 仅在点击生成按钮时触发组卷
    if start_assemble:
        with st.spinner(f"⚡ 正在为您智能组装试卷..."):
            # 候选池始终是全书；错题以"优先池 + 占比"注入，错题不足时自动用新题补齐
            candidate_pool = all_questions
            effective_ratio = wrong_ratio if subject_wrong_count > 0 else 0.0
            wrong_id_set = {q.id for q in subject_wrong_pool}
            if effective_ratio >= 0.99:
                target_title = f"考研数学《880》{current_subject.value} 错题专项重练卷"
            elif effective_ratio > 0:
                target_title = f"考研数学《880》{current_subject.value} 错题强化卷（错题{wrong_ratio_pct}%）"
            else:
                target_title = f"考研数学《880》{current_subject.value} 智能拼好卷"

            # 真题章节分布软权重:按强度 s 在基线1.0与真题考频权重w之间插值 eff=1+s*(w-1)
            chapter_weights: dict = {}
            if use_real_dist and real_dist_strength > 0:
                s = real_dist_strength
                for qt, chw in _dist_all.get(current_subject.value, {}).items():
                    chapter_weights[qt] = {ch: max(0.05, 1.0 + s * (w - 1.0)) for ch, w in chw.items()}

            engine = PaperEngine(candidate_pool)
            req = EngineRequest(
                title=target_title,
                subject=current_subject,
                mode=current_mode,
                target_chapters=default_target_chapters,
                enabled_categories=enabled_categories,
                category_weights=custom_category_weights,
                type_counts=custom_counts,
                difficulty_weights=diff_weights,
                tag_filter=selected_tag,
                seed=random.randint(1000, 99999),
                historical_covered_chapters=state_mgr.historical_covered_chapters,
                historical_seen_question_ids=state_mgr.historical_seen_ids,
                candidate_question_pool=candidate_pool,
                priority_pool_ids=wrong_id_set,
                priority_ratio=effective_ratio,
                exclude_seen=exclude_seen,
                chapter_weights=chapter_weights,
            )
            if current_mode == PaperMode.BUNDLE_3_PAPERS:
                bundle = engine.generate_bundle(req, bundle_size=3)
                st.session_state.current_bundle_p1 = bundle
                st.session_state.current_paper_p1 = bundle.papers[0]
                for p in bundle.papers:
                    state_mgr.record_paper_generation(p.questions)
                state_mgr.set_last_papers([[q.id for q in p.questions] for p in bundle.papers])
            else:
                paper = engine.generate_single_paper(req)
                st.session_state.current_paper_p1 = paper
                st.session_state.current_bundle_p1 = None
                state_mgr.record_paper_generation(paper.questions)
                state_mgr.set_last_papers([[q.id for q in paper.questions]])

    # 渲染当前生成的试卷
    active_paper: PaperItem | None = st.session_state.get("current_paper_p1")
    active_bundle: PaperBundle | None = st.session_state.get("current_bundle_p1")

    if not active_paper:
        st.markdown(
            f"""
            <div class="glass-card" style="text-align:center; padding: 48px 24px; margin-top:20px; background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);">
                <div style="font-size: 36px; margin-bottom: 12px;">🎯</div>
                <h3 style="color:#0f172a; font-size:18px; font-weight:800; margin-bottom:8px;">880 {current_subject.value} 拼卷大厅就绪</h3>
                <p style="color:#64748b; font-size:14px; max-width:640px; margin:0 auto 20px auto; line-height:1.6;">
                    系统默认从您标记的错题池中精准抽题重练。您也可以随时切换为全书模考。请调整上方规格后点击立即生成试卷。
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        if active_bundle:
            st.markdown(f"##### 📦 {active_bundle.title}")
            bundle_labels = [f"📄 {p.title}" for p in active_bundle.papers]
            selected_b_idx = st.radio(
                "分卷切换：",
                options=list(range(len(bundle_labels))),
                format_func=lambda i: bundle_labels[i],
                horizontal=True,
                key=f"p1_bundle_tabs_{current_subject.value}",
            )
            active_paper = active_bundle.papers[selected_b_idx]

        ai_t = AITutor(api_key=user_api_key, base_url=user_api_url, model=user_model_name)

        st.markdown("---")
        top_bar_c1, top_bar_c2 = st.columns([3.5, 1.2])
        with top_bar_c1:
            st.markdown(f"### 📝 {active_paper.title}")
            st.caption(f"卷号: {active_paper.paper_id} · 共 {active_paper.total_count} 题 · 选择 {len(active_paper.choice_questions)} 空 {len(active_paper.fill_questions)} 答 {len(active_paper.solution_questions)}")
        with top_bar_c2:
            show_all_ans = st.checkbox("📖 展开全部解析", value=True, key="p1_show_ans_cb")

        # 立即展示完整题目列表（0 毫秒即时呈现，不阻塞等待后台 PDF 编译）
        sections = [
            ("一、选择题", active_paper.choice_questions),
            ("二、填空题", active_paper.fill_questions),
            ("三、解答题", active_paper.solution_questions),
        ]

        # ---- 分页 + 局部刷新：点标错等按钮只 rerun 当前页卡片区（毫秒~百毫秒级），
        # 不再全量渲染配置区 + 全部题目 + PDF 面板 + 侧边栏。分页控件在 fragment 外。 ----
        _pq_flat: list = []
        for _t, _ql in sections:
            for _q in _ql:
                _pq_flat.append((_t, _q))
        _pq_total = len(_pq_flat)
        _pq_page_size = _pq_total
        _pq_total_pages = 1
        _pq_page = 1
        _pq_start = 0
        if _pq_total > 25:
            _ps_sel = st.selectbox("每页展示题数", ["25 题 (极速流畅)", "50 题", "全部展示（较慢）"], index=0, key=f"p1_pagesize_{current_subject.value}")
            _pq_page_size = 25 if _ps_sel.startswith("25") else (50 if _ps_sel.startswith("50") else _pq_total)
            _pq_total_pages = max(1, (_pq_total + _pq_page_size - 1) // _pq_page_size)
            _pq_page = st.number_input(f"当前页码 (共 {_pq_total_pages} 页 · {_pq_total} 题)", min_value=1, max_value=_pq_total_pages, value=1, step=1, key=f"p1_page_{current_subject.value}")
            _pq_start = (_pq_page - 1) * _pq_page_size
        _pq_end = min(_pq_total, _pq_start + _pq_page_size)
        _pq_cur = _pq_flat[_pq_start:_pq_end]

        @st.fragment()
        def render_paper_cards():
            _prev_t = None
            _qi = _pq_start
            for _t, q in _pq_cur:
                if _t != _prev_t:
                    st.markdown(f"#### {_t}")
                    _prev_t = _t
                _qi += 1
                is_active = state_mgr.is_in_active_pool(q.id)
                is_temp_mastered = state_mgr.is_temporarily_mastered(q.id)
                w_cnt = state_mgr.get_wrong_count(q.id)
                diff_b = "badge-basic" if q.difficulty == DifficultyLevel.BASIC else ("badge-adv" if q.difficulty == DifficultyLevel.ADVANCED else "badge-comp")

                with st.container(border=True):
                    # Header row with integrated top-right toggle & count buttons
                    if is_active:
                        c_h1, c_h2, c_h3 = st.columns([3.8, 1.4, 0.6])
                        with c_h1:
                            card_header_html = (
                                f'<div style="display:flex; align-items:center; margin-top:2px;">'
                                f'<span style="font-weight:800; font-size:16px; color:#000000; margin-right:4px;">{_qi}.</span>'
                                f'</div>'
                            )
                            st.markdown(card_header_html, unsafe_allow_html=True)
                        with c_h2:
                            st.button(f"❌ 移入历史错题", key=f"p1_arch_{q.id}_{_qi}_{current_subject.value}", type="primary", use_container_width=True, help="做题已掌握？点击移入历史错题档案（保留做错次数，不再强制抽取）", on_click=cb_archive_to_history, args=(q.id,))
                        with c_h3:
                            st.button("➕1", key=f"p1_inc_{q.id}_{_qi}_{current_subject.value}", use_container_width=True, help="又做错了？点击做错次数+1", on_click=cb_inc_wrong, args=(q.id, 1))

                    elif is_temp_mastered:
                        c_h1, c_h2, c_h3 = st.columns([3.8, 1.4, 0.9])
                        with c_h1:
                            card_header_html = (
                                f'<div style="display:flex; align-items:center; margin-top:2px;">'
                                f'<span style="font-weight:800; font-size:16px; color:#000000; margin-right:4px;">{_qi}.</span>'
                                f'</div>'
                            )
                            st.markdown(card_header_html, unsafe_allow_html=True)
                        with c_h2:
                            st.button("🎯 放回待练池", key=f"p1_react_{q.id}_{_qi}_{current_subject.value}", type="primary", use_container_width=True, help="点击重新放回活跃错题池参与组卷抽题", on_click=cb_reactivate_wrong, args=(q.id,))
                        with c_h3:
                            st.button("🗑️ 彻底删除", key=f"p1_del_{q.id}_{_qi}_{current_subject.value}", use_container_width=True, help="彻底从错题记录中移除", on_click=cb_remove_wrong, args=(q.id,))

                    else:
                        c_h1, c_h2 = st.columns([4.4, 1.2])
                        with c_h1:
                            card_header_html = (
                                f'<div style="display:flex; align-items:center; margin-top:2px;">'
                                f'<span style="font-weight:800; font-size:16px; color:#000000; margin-right:4px;">{_qi}.</span>'
                                f'</div>'
                            )
                            st.markdown(card_header_html, unsafe_allow_html=True)
                        with c_h2:
                            st.button("○ 标为错题", key=f"p1_mark_{q.id}_{_qi}_{current_subject.value}", use_container_width=True, help="做错了？点击放入待练错题池", on_click=cb_toggle_wrong, args=(q.id,))

                    # Question Stem（内联图片走 st.image,文字/公式/表格走 markdown)
                    render_stem(q.stem)

                    # Options
                    if q.options:
                        oc1, oc2 = st.columns(2)
                        for oi, opt in enumerate(q.options):
                            if oi % 2 == 0:
                                with oc1: st.markdown(opt)
                            else:
                                with oc2: st.markdown(opt)

                    # Solutions Callout (含书籍、难度、章节、ID、题目标签与做错统计)
                    if show_all_ans or st.checkbox(f"查看答案与解析", key=f"p1_ans_cb_{q.id}_{_qi}_{current_subject.value}"):
                        tags_html = "".join(f'<span class="badge badge-tag">#{t}</span>' for t in q.tags) if q.tags else ""
                        if is_active:
                            if w_cnt >= 2:
                                status_badge = f'<span class="badge" style="background:#fff1f2; color:#be123c; border:1px solid #fda4af; font-weight:700;">🔥 顽固错题 · 累计做错 {w_cnt} 次</span>'
                            else:
                                status_badge = f'<span class="badge badge-adv" style="font-weight:700;">🎯 待练错题 · 累计做错 {w_cnt} 次</span>'
                        elif is_temp_mastered:
                            status_badge = f'<span class="badge" style="background:#fef3c7; color:#92400e; border:1px solid #fcd34d; font-weight:700;">🏆 历史错题 · 历史做错 {w_cnt} 次</span>'
                        else:
                            status_badge = ''

                        meta_row = (
                            f'<div style="display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:8px;">'
                            f'<span class="badge badge-ch">《{getattr(q, "book", "880")}》</span>'
                            + diff_badge(q)
                            + f'<span class="badge badge-ch">{q.chapter}</span>'
                            f'<span style="font-size:11.5px; color:#64748b; font-family:monospace; margin-right:4px;">ID: {q.id}</span>'
                            f'{tags_html} {status_badge}'
                            f'</div>'
                        )

                        st.markdown(meta_row, unsafe_allow_html=True)
                        if q.answer: st.markdown(f"**【参考答案】**：`{q.answer}`")
                        if q.solution: st.markdown(f"**【详细解析】**：\n{q.solution}")

                    with st.expander("🤖 呼叫 AI 名师解答"):
                        if st.button("🚀 运行 AI 详细推导", key=f"p1_ai_btn_{q.id}_{_qi}_{current_subject.value}"):
                            # 缓存优先：已生成过解析（含详细解析 PDF 产物）→ 直接显示缓存，0 次 API
                            _cached_sol = ""
                            _cached_status = ""
                            try:
                                from core.ai_solutions import load_cache, stem_fingerprint
                                _ce = load_cache().get(q.id)
                                if _ce and (_ce.get("solution") or "").strip() and _ce.get("stem_fp") == stem_fingerprint(q):
                                    _cached_sol = _ce["solution"]
                                    _cached_status = _ce.get("review_status") or ""
                            except Exception:
                                _cached_sol = ""
                            if _cached_sol:
                                st.markdown(_cached_sol)
                                if _cached_status == "unchecked":
                                    st.caption("（来自本地缓存 · 未经审核校验 · 未消耗 API）")
                                elif _cached_status in ("verified", "fixed", "human"):
                                    st.caption("（来自本地缓存 · 已审核 · 未消耗 API）")
                                else:
                                    st.caption("（来自本地缓存 · 未消耗 API）")
                            else:
                                with st.spinner("AI 名师正在严密演算推导..."):
                                    box = st.empty()
                                    res_text = ""
                                    for chunk in ai_t.solve_question_stream(q):
                                        res_text += chunk
                                        box.markdown(res_text)
                                # 生成完成 → 写缓存（标 unchecked；下载解析 PDF 时复用解析、仅重审一次，不重复生成）
                                if (res_text or "").strip():
                                    try:
                                        from datetime import datetime
                                        from core.ai_solutions import load_cache, save_cache, _lock, stem_fingerprint
                                        with _lock:
                                            _c = load_cache()
                                            _e = _c.get(q.id)
                                            if _e is None:
                                                _e = {"answer": "", "solution": "", "review_status": "unchecked",
                                                      "stem_fp": stem_fingerprint(q)}
                                                _c[q.id] = _e
                                            if not (_e.get("solution") or "").strip():
                                                _e["solution"] = res_text
                                                _e["review_status"] = "unchecked"
                                                _e["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                save_cache(_c)
                                    except Exception:
                                        pass

        render_paper_cards()

        # 题目下方按需导出 PDF 专区
        st.markdown("---")
        st.markdown("### 📥 导出与归档试卷 PDF")

        q_ids_tuple = tuple(q.id for q in active_paper.questions)

        # 首屏性能红线:st.download_button 的 data= 是【急切求值】—— 只要 session 里存着试卷,
        # 每次冷缓存重跑都会当场渲 3 份 PDF(无头浏览器排版,实测 ≈18s),而 st.tabs 会执行完
        # 所有标签页,于是「逐题标错」等页面全被堵住。故改为:点「生成」后才渲染下载按钮。
        _pdf_ready_key = f"p1_pdf_ready_{active_paper.paper_id}"
        if not st.session_state.get(_pdf_ready_key):
            gc1, gc2 = st.columns([1.6, 2.4])
            with gc1:
                if st.button(
                    "📦 生成 3 种版式 PDF",
                    type="primary",
                    use_container_width=True,
                    key=f"p1_pdf_gen_{active_paper.paper_id}",
                ):
                    st.session_state[_pdf_ready_key] = True
                    st.rerun()
            with gc2:
                st.caption("💡 生成约十几秒")
        else:
            tb1, tb2, tb3 = st.columns(3)
            with tb1:
                _pdf_real = _safe_pdf_bytes(
                    active_paper.paper_id, active_paper.title, q_ids_tuple, edition_str=PDFEdition.REAL_EXAM.value, subject_str=current_subject.value
                )
                if _pdf_real:
                    st.download_button(
                        "📥 下载真题版 PDF",
                        data=_pdf_real,
                        file_name=f"{active_paper.paper_id}_真题版试卷.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key=f"p1_down_real_{active_paper.paper_id}",
                    )
            with tb2:
                _pdf_wb = _safe_pdf_bytes(
                    active_paper.paper_id, active_paper.title, q_ids_tuple, edition_str=PDFEdition.WORKBOOK_A4.value, subject_str=current_subject.value
                )
                if _pdf_wb:
                    st.download_button(
                        "📝 下载 A4 做题本 PDF",
                        data=_pdf_wb,
                        file_name=f"{active_paper.paper_id}_A4做题本.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key=f"p1_down_wb_{active_paper.paper_id}",
                    )
            with tb3:
                # 详细解析版：点击下载时才触发 AI 名师补全缺失答案解析（做题阶段不等待）
                _ai_ready_key = f"p1_ai_ready_{active_paper.paper_id}"
                _missing_now = [q for q in active_paper.questions if needs_solution(q)]
                if user_api_key and _missing_now and not st.session_state.get(_ai_ready_key):
                    if st.button(
                        "📥 下载详细解析版 PDF（AI 补全解析）",
                        use_container_width=True,
                        key=f"p1_sol_ai_gen_{active_paper.paper_id}",
                        help="点击后 AI 名师先补齐缺失的答案解析（有缓存直接复用），完成后自动出下载按钮。",
                    ):
                        _bar = st.progress(0.0, text=f"AI 名师正在生成 {len(_missing_now)} 道题的答案解析...")
                        def _cb(done, total, qid, status):
                            if status == "generated":
                                _bar.progress(done / total, text=f"AI 名师正在生成答案解析 ({done}/{total})：{qid}")
                            elif status == "cached":
                                _bar.progress(done / total, text=f"复用已有 AI 解析缓存 ({done}/{total})...")
                            elif isinstance(status, str) and status.startswith("pdf_detail:"):
                                _bar.progress(done / total, text=f"AI 解题失败，正在扫描解析 PDF 定位答案 ({done}/{total})：{qid}（{status[12:]}）")
                            elif status == "pdf":
                                _bar.progress(done / total, text=f"AI 解题失败，正在扫描解析 PDF 定位答案 ({done}/{total})：{qid}")
                            elif status == "reviewing":
                                _bar.progress(done / total, text=f"AI 阅卷专家正在独立审核答案 ({done}/{total})：{qid}")
                            elif status == "failed":
                                _bar.progress(done / total, text=f"AI 解题失败且扫描版未命中 ({done}/{total})：{qid}")
                            elif status == "review_failed":
                                _bar.progress(done / total, text=f"AI 审核失败，已标记未审核 ({done}/{total})：{qid}")
                        try:
                            gen, cached = ensure_solutions(active_paper.questions, _build_solution_tutor(user_api_key, user_api_url, user_model_name), progress_cb=_cb)
                            if gen > 0:
                                get_cached_pdf.clear()  # 旧版可能是「略」，清缓存强制带 AI 解析重生成
                        except Exception:
                            pass
                        st.session_state[_ai_ready_key] = True
                        st.rerun()
                else:
                    _pdf_sol = _safe_pdf_bytes(
                        active_paper.paper_id, active_paper.title, q_ids_tuple, edition_str=PDFEdition.SOLUTION.value, subject_str=current_subject.value
                    )
                    if _pdf_sol:
                        st.download_button(
                            "📑 下载详细解析版 PDF",
                            data=_pdf_sol,
                            file_name=f"{active_paper.paper_id}_详细解析.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                            key=f"p1_down_sol_{active_paper.paper_id}",
                        )

        tb4, _tb_pad = st.columns([1.6, 2.4])
        with tb4:
            # 归档写的是"服务器本地磁盘"：云端硬盘临时、多用户共用、用户也拿不到 → 仅本地部署有意义
            if IS_CLOUD:
                st.caption("💡 云端无本地归档，请用左侧下载按钮直接存到你的设备。")
            elif st.button("💾 归档到本地试卷库", use_container_width=True, key=f"p1_archive_btn_{active_paper.paper_id}"):
                with st.spinner("正在生成并归档 3 种版式 PDF..."):
                    real_pdf = _safe_pdf_bytes(active_paper.paper_id, active_paper.title, q_ids_tuple, edition_str=PDFEdition.REAL_EXAM.value, subject_str=current_subject.value)
                    wb_pdf = _safe_pdf_bytes(active_paper.paper_id, active_paper.title, q_ids_tuple, edition_str=PDFEdition.WORKBOOK_A4.value, subject_str=current_subject.value)
                    sol_pdf = _safe_pdf_bytes(active_paper.paper_id, active_paper.title, q_ids_tuple, edition_str=PDFEdition.SOLUTION.value, subject_str=current_subject.value)
                    p_dir = Path("试卷库")
                    p_dir.mkdir(exist_ok=True)
                    (p_dir / f"{active_paper.paper_id}_真题版试卷.pdf").write_bytes(real_pdf)
                    (p_dir / f"{active_paper.paper_id}_A4做题本.pdf").write_bytes(wb_pdf)
                    (p_dir / f"{active_paper.paper_id}_详细解析.pdf").write_bytes(sol_pdf)
                    st.success("✓ 已成功归档 3 种版式 PDF 至 `试卷库/` 文件夹！")


# -------------------------------------------------------------------------
# WORKSPACE 2: 题库逐题标错中枢
# -------------------------------------------------------------------------
elif active_module == "🏷️ 题库逐题标错":
    # 1. 顶部标错书籍选择 (让不同的书来标记)
    available_target_books = selected_books if selected_books else available_books
    m_top_c1, m_top_c2 = st.columns([1.5, 3.5])
    with m_top_c1:
        target_book = st.selectbox(
            "选择标错书籍",
            options=available_target_books,
            index=0,
            key=f"p2_target_book_{current_subject.value}",
            help="选择需要逐题打标错题的参考书籍",
        )
    with m_top_c2:
        st.caption(f"当前正在标错：《{target_book}》 · {current_subject.value}。在各章节中找到做错的题目并打标，系统会自动收录入错题池用于智能拼卷重练。")

    # 当前书籍对应的题目与真实章节划分
    book_questions = [q for q in raw_questions if getattr(q, "book", "880") == target_book]
    # 分组维度:真题按「年份」,880 等按「章节」。dim_of(q) 取该题的维度值。
    is_zhenti = any(getattr(q, "year", "") for q in book_questions)

    # 篇筛选:1000题分基础篇/强化篇/综合篇,且不同篇/线代概率间章号会重复,
    # 必须先选篇再选章,否则同名"第1章"会混装。仅当书籍带 pian 时显示。
    book_pians = []
    seen_p = set()
    for q in book_questions:
        p = getattr(q, "pian", "") or ""
        if p and p not in seen_p:
            seen_p.add(p)
            book_pians.append(p)
    PIAN_ORDER = {"基础篇": 0, "强化篇": 1, "提高篇": 1, "综合提高篇": 1, "综合篇": 2}
    book_pians.sort(key=lambda p: PIAN_ORDER.get(p, 9))
    target_pian = None
    if book_pians:
        target_pian = st.selectbox(
            "选择篇章",
            options=book_pians,
            index=0,
            key=f"p2_pian_select_{current_subject.value}_{target_book}",
            help="张宇1000题分基础篇/强化篇/综合篇,先选篇再选章节",
        )
        book_questions = [q for q in book_questions if (getattr(q, "pian", "") or "") == target_pian]

    dim_label = "选择年份" if is_zhenti else "选择章节"
    def dim_of(q):
        return (q.year if is_zhenti else q.chapter) or ""
    seen_dim = set()
    book_dims = []
    for q in book_questions:
        d = dim_of(q)
        if d and d not in seen_dim:
            seen_dim.add(d)
            book_dims.append(d)
    if is_zhenti:
        book_dims.sort()  # 年份旧→新,2010 在最前
    if not book_dims:
        book_dims = loaded_chapters

    # Filters —— 1000题:篇已表达难度分层,去掉冗余的「难度分层」下拉,只留 章/题型/状态。
    _has_pian = bool(book_pians)
    if _has_pian:
        target_diff = "全部"
        m_col1, m_col3, m_col4 = st.columns([1.8, 1, 1.2])
    else:
        m_col1, m_col2, m_col3, m_col4 = st.columns([1.8, 1, 1, 1.2])
    with m_col1:
        target_ch = st.selectbox(
            dim_label,
            options=book_dims,
            index=0,
            key=f"p2_ch_select_{current_subject.value}_{target_book}_{target_pian or ''}",
        )
    if not _has_pian:
        with m_col2:
            target_diff = st.selectbox("难度分层", options=["全部", "基础题", "综合题", "拓展题"], index=0, key=f"p2_diff_select_{current_subject.value}")
    with m_col3:
        target_type = st.selectbox("题型筛选", options=["全部", "选择题", "填空题", "解答题"], index=0, key=f"p2_type_select_{current_subject.value}")
    with m_col4:
        target_status = st.selectbox("错题状态", options=["全部题目", "🎯 仅看待练错题", "🏆 仅看历史错题", "🔥 仅看顽固错题", "⭐ 仅看所有曾错题", "✅ 仅看未错题"], index=0, key=f"p2_status_select_{current_subject.value}")

    # 本章多维统计指示条
    all_ch_qs = [q for q in book_questions if dim_of(q) == target_ch]
    ch_active_n = sum(1 for q in all_ch_qs if state_mgr.is_in_active_pool(q.id))
    ch_stubborn_n = sum(1 for q in all_ch_qs if state_mgr.is_in_active_pool(q.id) and state_mgr.get_wrong_count(q.id) >= 2)
    ch_past_n = sum(1 for q in all_ch_qs if state_mgr.is_temporarily_mastered(q.id))

    ch_stats_html = (
        f'<div style="display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin:8px 0 12px 0; padding:8px 12px; background:#f8fafc; border-radius:8px; border:1px solid #e2e8f0;">'
        f'<span style="font-weight:700; color:#1e293b;">📖 《{target_book}》{target_ch} · 共 {len(all_ch_qs)} 题</span>'
        f'<span class="badge badge-adv" style="font-weight:700;">🎯 待练错题: {ch_active_n} 题</span>'
        f'<span class="badge" style="background:#fef3c7; color:#92400e; border:1px solid #fcd34d; font-weight:700;">🏆 历史错题: {ch_past_n} 题</span>'
        f'<span class="badge" style="background:#fff1f2; color:#be123c; border:1px solid #fda4af; font-weight:700;">🔥 顽固错题: {ch_stubborn_n} 题</span>'
        f'</div>'
    )
    st.markdown(ch_stats_html, unsafe_allow_html=True)

    # Filtered Questions (严格按 基础题 -> 综合题 -> 拓展题 排序)
    diff_order_list = (DifficultyLevel.BASIC, DifficultyLevel.COMPREHENSIVE, DifficultyLevel.ADVANCED)
    
    if target_status == "🎯 仅看待练错题":
        status_check = lambda q: state_mgr.is_in_active_pool(q.id)
    elif target_status == "🏆 仅看历史错题":
        status_check = lambda q: state_mgr.is_temporarily_mastered(q.id)
    elif target_status == "🔥 仅看顽固错题":
        status_check = lambda q: state_mgr.is_in_active_pool(q.id) and state_mgr.get_wrong_count(q.id) >= 2
    elif target_status == "⭐ 仅看所有曾错题":
        status_check = lambda q: state_mgr.is_wrong_marked(q.id)
    elif target_status == "✅ 仅看未错题":
        status_check = lambda q: not state_mgr.is_wrong_marked(q.id)
    else:
        status_check = lambda q: True

    ch_questions = [
        q for q in book_questions
        if dim_of(q) == target_ch
        and (target_diff == "全部" or target_diff in q.difficulty.value)
        and (target_type == "全部" or target_type == q.question_type.value)
        and status_check(q)
    ]
    if is_zhenti:
        # 真题按原卷题号序(题型→序号),不掺难度,避免同题型内基础/综合错位
        ch_questions.sort(key=lambda q: parse_qid_tuple(q.id))
    else:
        ch_questions.sort(key=lambda q: (
            diff_order_list.index(q.difficulty) if q.difficulty in diff_order_list else 9,
            (QuestionType.CHOICE, QuestionType.FILL_BLANK, QuestionType.SOLUTION).index(q.question_type) if q.question_type in (QuestionType.CHOICE, QuestionType.FILL_BLANK, QuestionType.SOLUTION) else 9,
            q.id,
        ))

    # 本章全部题目(不受上方难度/题型/状态筛选影响),用于批量标错的题号匹配
    chapter_all = [q for q in book_questions if dim_of(q) == target_ch]
    _SEC_SHORT = {DifficultyLevel.BASIC: "基础", DifficultyLevel.COMPREHENSIVE: "综合", DifficultyLevel.ADVANCED: "拓展"}
    _TYPE_SHORT = {QuestionType.CHOICE: "选", QuestionType.FILL_BLANK: "填", QuestionType.SOLUTION: "解"}
    _TYPE_LABEL = {"选": "选择", "填": "填空", "解": "解答"}
    _SEC_ICON = {"基础": "🟢 基础篇", "综合": "🔵 综合篇", "拓展": "🟣 拓展篇"}
    # 1000题(带篇):章内整体顺序编号,题号不分题型 → 单框;篇已在上方选。
    is_1000 = bool(book_pians)
    # (篇, 题型) 组合 → 题目。真题/1000题篇塌缩为空("")→ 每题型只一框、题号题型内连续。
    combos: dict[tuple[str, str], list] = {}
    for q in chapter_all:
        if is_1000:
            combos.setdefault(("", ""), []).append(q)   # 单桶:章内顺序号,不分题型
        else:
            sec_key = "" if is_zhenti else _SEC_SHORT.get(q.difficulty, "综合")
            combos.setdefault((sec_key, _TYPE_SHORT.get(q.question_type, "选")), []).append(q)

    def _resolve_ids(inputs: dict[tuple[str, str], str]) -> list[str]:
        """把各 (篇,题型) 框里的题号解析成精确题目 ID。序号取 ID 末段:
        880 四段 01-基础-选-03 → '03';真题三段 2010-选-01 → '01'(兼容不越界)。"""
        ids: list[str] = []
        for (sec, typ), text in inputs.items():
            present = {int(q.id.split("-")[-1]): q.id for q in combos.get((sec, typ), []) if q.id.split("-")[-1].isdigit()}
            for n in re.findall(r"\d+", text or ""):
                qid = present.get(int(n))
                if qid:
                    ids.append(qid)
        return ids

    # Quick Batch Marker Box & Export
    with st.expander("⚡ 批量标错与数据导出", expanded=True):
        if is_1000:
            _batch_hint = "刷完一章后，输入该章原书题号（章内整体顺序编号，不分题型），用逗号或空格隔开："
        elif is_zhenti:
            _batch_hint = "按 **题型** 分别输入该年真题题号（每个题型各自从 1 编号），用逗号或空格隔开："
        else:
            _batch_hint = "刷完一章后，按 **篇 × 题型** 分别输入原书题号（每个题型各自从 1 编号），用逗号或空格隔开："
        st.markdown(_batch_hint)
        batch_inputs: dict[tuple[str, str], str] = {}
        if is_1000:
            # 1000题:单框,章内整体顺序题号
            n_q = len(combos.get(("", ""), []))
            batch_inputs[("", "")] = st.text_input(
                f"题号（1-{n_q}）",
                placeholder="如: 1, 3, 5",
                key=f"p2_in_seq_{current_subject.value}_{target_book}_{target_pian or ''}_{target_ch}",
            )
        else:
            # 真题:单一空篇桶(每题型一框);880:三篇各一组
            _secs = [""] if is_zhenti else ["基础", "综合", "拓展"]
            for sec in _secs:
                sec_types = [t for t in ("选", "填", "解") if (sec, t) in combos]
                if not sec_types:
                    continue
                if not is_zhenti:  # 真题无篇分层,不显示篇标题
                    st.markdown(f"**{_SEC_ICON[sec]}**")
                cols = st.columns(len(sec_types))
                for col, typ in zip(cols, sec_types):
                    with col:
                        n_q = len(combos[(sec, typ)])
                        batch_inputs[(sec, typ)] = st.text_input(
                            f"{_TYPE_LABEL[typ]}题（1-{n_q}）",
                            placeholder="如: 1, 3, 5",
                            # key 含章节：切章节即换一组全新空框，不残留上一章敲的题号
                            key=f"p2_in_{sec}_{typ}_{current_subject.value}_{target_ch}",
                        )

        b_c1, b_c2, b_c3 = st.columns(3)
        with b_c1:
            if st.button("➕ 批量标记错题", type="primary", use_container_width=True, key=f"p2_batch_add_{current_subject.value}"):
                to_mark_ids = _resolve_ids(batch_inputs)
                if to_mark_ids:
                    state_mgr.batch_mark_wrong(to_mark_ids)
                    st.success(f"✓ 成功标记 {len(to_mark_ids)} 道错题！")
                    st.rerun()
                else:
                    st.warning("未匹配到有效题号，请检查输入的数字。")
        with b_c2:
            if st.button("🧹 批量移除错题", use_container_width=True, key=f"p2_batch_remove_{current_subject.value}"):
                to_unmark_ids = _resolve_ids(batch_inputs)
                if to_unmark_ids:
                    state_mgr.batch_unmark_wrong(to_unmark_ids)
                    st.success(f"✓ 成功移除 {len(to_unmark_ids)} 道题目！")
                    st.rerun()
        with b_c3:
            wrong_json = state_mgr.export_wrong_questions_json()
            st.download_button(
                f"📥 导出错题本备份",
                data=wrong_json.encode("utf-8"),
                file_name=f"我的880_{current_subject.value}_错题本备份.json",
                mime="application/json",
                use_container_width=True,
                key=f"p2_down_json_{current_subject.value}",
            )

        st.markdown("---")
        st.markdown("**📤 导入错题本备份**：上传此前导出的 JSON，跨设备 / 跨版本恢复错题（当前题库已不存在的题号会自动跳过）。")
        imp_c1, imp_c2 = st.columns([3, 1.4])
        with imp_c1:
            uploaded_backup = st.file_uploader(
                "选择错题本备份 JSON",
                type=["json"],
                key=f"p2_import_file_{current_subject.value}",
                label_visibility="collapsed",
            )
        with imp_c2:
            import_merge = st.checkbox("合并到现有", value=True, key=f"p2_import_merge_{current_subject.value}", help="勾选=与当前错题合并；取消=先清空再导入")
        if uploaded_backup is not None:
            if st.button("📤 确认导入", type="primary", use_container_width=True, key=f"p2_import_btn_{current_subject.value}"):
                try:
                    raw = uploaded_backup.getvalue().decode("utf-8")
                    valid_ids = {q.id for q in raw_questions}
                    imported, skipped = state_mgr.import_wrong_questions_json(raw, valid_ids=valid_ids, merge=import_merge)
                    msg = f"✓ 成功导入 {imported} 道错题！"
                    if skipped:
                        msg += f" {skipped} 道因当前题库无此题号被跳过。"
                    st.success(msg)
                    st.rerun()
                except Exception as e:
                    st.error(f"导入失败：备份文件格式不正确（{e}）")

    # 分页控制器 (大幅减少组件渲染数量，消除 WebSocket 拥堵)
    total_q_count = len(ch_questions)
    if total_q_count > 25:
        pg_c1, pg_c2 = st.columns([1.5, 3.5])
        with pg_c1:
            page_size_str = st.selectbox("每页展示题数", ["25 题 (极速流畅)", "50 题", "全部展示（题多时较慢，建议分页）"], index=0, key=f"p2_pagesize_{current_subject.value}")
        
        if page_size_str.startswith("25"):
            page_size = 25
        elif page_size_str.startswith("50"):
            page_size = 50
        else:
            page_size = total_q_count

        total_pages = max(1, (total_q_count + page_size - 1) // page_size)
        with pg_c2:
            current_page = st.number_input(f"当前页码 (共 {total_pages} 页 · {total_q_count} 题)", min_value=1, max_value=total_pages, value=1, step=1, key=f"p2_page_{current_subject.value}")
        
        start_idx = (current_page - 1) * page_size
        end_idx = min(total_q_count, start_idx + page_size)
        display_questions = ch_questions[start_idx:end_idx]
    else:
        display_questions = ch_questions

    # Question Cards List in Chapter —— fragment 化：点「标为错题/移入历史/➕1」等按钮
    # 只局部刷新本卡片区（毫秒级），不再重跑整个脚本（其它 tab/侧边栏/URL 同步全部跳过）。
    # 筛选/分页控件在 fragment 外，改动时仍走全量 rerun 重建本区。
    @st.fragment()
    def render_marker_cards():
        # Question Cards List in Chapter (按 篇 -> 题型 分组展示,题号取 ID 第4段=原书题型内序号)
        st.markdown(f"#### 📖 {target_ch} · 共 {len(ch_questions)} 题")

        _SEC_ORDER =[(DifficultyLevel.BASIC, "🟢 基础篇"), (DifficultyLevel.COMPREHENSIVE, "🔵 综合篇"), (DifficultyLevel.ADVANCED, "🟣 拓展篇")]
        _TYPE_ORDER = [(QuestionType.CHOICE, "选择题"), (QuestionType.FILL_BLANK, "填空题"), (QuestionType.SOLUTION, "解答题")]

        def _seq_of(qid: str) -> int:
            parts = qid.split("-")
            # 序号取末段:880 四段取第4段,真题三段取第3段,均为题型内序号
            return int(parts[-1]) if parts and parts[-1].isdigit() else 0

        sections_to_show = []
        if is_zhenti:
            # 真题无篇分层,直接按题型分组(标题只显示题型)
            for qtype, type_label in _TYPE_ORDER:
                grp = [q for q in display_questions if q.question_type == qtype]
                if grp:
                    sections_to_show.append((f"📝 {type_label}", grp))
        else:
            for diff, sec_icon in _SEC_ORDER:
                for qtype, type_label in _TYPE_ORDER:
                    grp = [q for q in display_questions if q.difficulty == diff and q.question_type == qtype]
                    if grp:
                        sections_to_show.append((f"{sec_icon} · {type_label}", grp))

        for sec_title, sec_q_list in sections_to_show:
            # 总数/已标错取本章该组全量(chapter_all),不受分页与状态筛选影响。
            # 真题按题型全量(无篇);880 按 篇·题型 全量。
            diff0, qtype0 = sec_q_list[0].difficulty, sec_q_list[0].question_type
            if is_zhenti:
                full_group = [q for q in chapter_all if q.question_type == qtype0]
            else:
                full_group = [q for q in chapter_all if q.difficulty == diff0 and q.question_type == qtype0]
            sec_total = len(full_group)
            sec_wrong_n = sum(1 for q in full_group if state_mgr.is_wrong_marked(q.id))
            st.markdown(f"##### {sec_title} · 共 {sec_total} 题 · 已标错 {sec_wrong_n} 题")

            for q in sec_q_list:
                q_idx_in_sec = _seq_of(q.id)
                is_active = state_mgr.is_in_active_pool(q.id)
                is_temp_mastered = state_mgr.is_temporarily_mastered(q.id)
                w_cnt = state_mgr.get_wrong_count(q.id)
                diff_cls = "badge-basic" if q.difficulty == DifficultyLevel.BASIC else ("badge-adv" if q.difficulty == DifficultyLevel.ADVANCED else "badge-comp")

                with st.container(border=True):
                    # Header Row with integrated top-right toggle & count buttons
                    if is_active:
                        th1, th2, th3 = st.columns([3.8, 1.4, 0.6])
                        with th1:
                            w2_header_html = (
                                f'<div style="display:flex; align-items:center; margin-top:2px;">'
                                f'<span style="font-weight:800; font-size:15px; color:#000000; margin-right:4px;">{q_idx_in_sec}.</span>'
                                f'</div>'
                            )
                            st.markdown(w2_header_html, unsafe_allow_html=True)
                        with th2:
                            st.button("❌ 移入历史错题", key=f"p2_arch_{q.id}_{current_subject.value}", type="primary", use_container_width=True, help="做题已掌握？点击移入历史错题档案（保留做错次数，不再强制抽取）", on_click=cb_archive_to_history, args=(q.id,))
                        with th3:
                            st.button("➕1", key=f"p2_inc_{q.id}_{current_subject.value}", use_container_width=True, help="又做错了？点击做错次数+1", on_click=cb_inc_wrong, args=(q.id, 1))

                    elif is_temp_mastered:
                        th1, th2, th3 = st.columns([3.8, 1.4, 0.9])
                        with th1:
                            w2_header_html = (
                                f'<div style="display:flex; align-items:center; margin-top:2px;">'
                                f'<span style="font-weight:800; font-size:15px; color:#000000; margin-right:4px;">{q_idx_in_sec}.</span>'
                                f'</div>'
                            )
                            st.markdown(w2_header_html, unsafe_allow_html=True)
                        with th2:
                            st.button("🎯 放回待练池", key=f"p2_react_{q.id}_{current_subject.value}", type="primary", use_container_width=True, help="点击重新放回活跃错题池参与组卷抽题", on_click=cb_reactivate_wrong, args=(q.id,))
                        with th3:
                            st.button("🗑️ 彻底删除", key=f"p2_del_{q.id}_{current_subject.value}", use_container_width=True, help="彻底从错题记录中移除", on_click=cb_remove_wrong, args=(q.id,))

                    else:
                        th1, th2 = st.columns([4.4, 1.2])
                        with th1:
                            w2_header_html = (
                                f'<div style="display:flex; align-items:center; margin-top:2px;">'
                                f'<span style="font-weight:800; font-size:15px; color:#000000; margin-right:4px;">{q_idx_in_sec}.</span>'
                                f'</div>'
                            )
                            st.markdown(w2_header_html, unsafe_allow_html=True)
                        with th2:
                            st.button("○ 标为错题", key=f"p2_toggle_{q.id}_{current_subject.value}", use_container_width=True, help="做错了？点击放入待练错题池", on_click=cb_toggle_wrong, args=(q.id,))

                    # 内联图片走 st.image,文字/公式/表格走 markdown
                    render_stem(q.stem)
                    if q.options:
                        mc1, mc2 = st.columns(2)
                        for oi, opt in enumerate(q.options):
                            if oi % 2 == 0:
                                with mc1: st.markdown(opt)
                            else:
                                with mc2: st.markdown(opt)

                    with st.expander("查看答案与解析"):
                        tags_html = "".join(f'<span class="badge badge-tag">#{t}</span>' for t in q.tags) if q.tags else ""
                        if is_active:
                            if w_cnt >= 2:
                                status_badge = f'<span class="badge" style="background:#fff1f2; color:#be123c; border:1px solid #fda4af; font-weight:700;">🔥 顽固错题 · 累计做错 {w_cnt} 次</span>'
                            else:
                                status_badge = f'<span class="badge badge-adv" style="font-weight:700;">🎯 待练错题 · 累计做错 {w_cnt} 次</span>'
                        elif is_temp_mastered:
                            status_badge = f'<span class="badge" style="background:#fef3c7; color:#92400e; border:1px solid #fcd34d; font-weight:700;">🏆 历史错题 · 历史做错 {w_cnt} 次</span>'
                        else:
                            status_badge = ''

                        meta_tags_row = (
                            f'<div style="display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:8px;">'
                            f'<span class="badge badge-ch">《{getattr(q, "book", "880")}》</span>'
                            + diff_badge(q)
                            + f'<span class="badge badge-ch">{q.chapter}</span>'
                            f'<span class="badge badge-ch">{q.question_type.value}</span>'
                            f'<span style="font-size:11.5px; color:#64748b; font-family:monospace; margin-right:4px;">ID: {q.id}</span>'
                            f'{tags_html} {status_badge}'
                            f'</div>'
                        )
                        st.markdown(meta_tags_row, unsafe_allow_html=True)
                        if q.answer: st.markdown(f"**【参考答案】**：`{q.answer}`")
                        if q.solution: st.markdown(f"**【详细解析】**：\n{q.solution}")

    render_marker_cards()

    # ---- 底部翻页导航：一页显示所有题太卡时，切 25/50 分页逐页标错 ----
    if total_q_count > 25 and total_pages > 1:
        st.markdown("---")
        # 用 on_click 回调改页码：回调在按钮点击后、脚本 rerun 前执行，此时顶部 number_input
        # 尚未实例化，写其 key 安全；直接 if 分支写会在 widget 实例化后触发
        # StreamlitWidgetAlreadyInstantiatedError。
        _nav_key = f"p2_page_{current_subject.value}"
        # 权威总页数：非 widget key，每次 rerun 刷新，回调读取时永远最新
        _nav_total_key = f"p2_pages_total_{current_subject.value}"
        st.session_state[_nav_total_key] = total_pages

        def _nav_prev():
            _cur = int(st.session_state.get(_nav_key, 1))
            st.session_state[_nav_key] = max(1, _cur - 1)

        def _nav_next():
            _cur = int(st.session_state.get(_nav_key, 1))
            _t = int(st.session_state.get(_nav_total_key, total_pages))
            st.session_state[_nav_key] = min(_t, _cur + 1)

        nav1, nav2, nav3, nav4 = st.columns([1, 1, 2.2, 1.3])
        with nav1:
            st.button("⬅️ 上一页", key=f"p2_prev_{current_subject.value}", use_container_width=True,
                      disabled=(current_page <= 1), on_click=_nav_prev)
        with nav2:
            st.button("下一页 ➡️", key=f"p2_next_{current_subject.value}", use_container_width=True,
                      disabled=(current_page >= total_pages), on_click=_nav_next)
        with nav3:
            st.caption(f"第 {current_page} / {total_pages} 页 · 每页 {page_size} 题 · 共 {total_q_count} 题（顶部可改每页题数）")
        with nav4:
            if st.button("⬆️ 返回顶部筛选", key=f"p2_top_{current_subject.value}", use_container_width=True):
                st.session_state[f"p2_scroll_top_{current_subject.value}"] = True
                st.rerun()
        if st.session_state.get(f"p2_scroll_top_{current_subject.value}"):
            st.components.v1.html(
                "<script>setTimeout(function(){var el=window.parent.document.querySelector('section.main, section[data-testid=\"stMain\"], [data-testid=\"stMainBlockContainer\"]');if(el){el.scrollTo({top:0,behavior:'smooth'});}},120);</script>",
                height=0,
            )
            st.session_state[f"p2_scroll_top_{current_subject.value}"] = False


# -------------------------------------------------------------------------
# WORKSPACE 3: 我的错题本 (全科错题总览，看具体是哪几道)
# -------------------------------------------------------------------------
elif active_module == "📕 我的错题本":
    st.markdown(f"### 📕 {current_subject.value} 错题本 · 全科错题一览")

    # 全科所有曾错题(含已归档历史);按 待练/顽固/历史 分类
    wb_all = [q for q in all_questions if q.id in all_wrong_ids]

    def _wb_status(qid: str) -> str:
        """行标签用:最具体的状态 'stubborn' | 'active' | 'history' | ''。
        顽固是待练的子集(做错≥2),历史为已归档。"""
        if state_mgr.is_in_active_pool(qid):
            return "stubborn" if state_mgr.get_wrong_count(qid) >= 2 else "active"
        if state_mgr.is_temporarily_mastered(qid):
            return "history"
        return ""

    # 概览语义与侧边栏画像一致:待练=全部活跃错题(含顽固);顽固=其中做错≥2的子集;历史=已归档
    wb_active_all = [q for q in wb_all if state_mgr.is_in_active_pool(q.id)]
    wb_stubborn = [q for q in wb_active_all if state_mgr.get_wrong_count(q.id) >= 2]
    wb_history = [q for q in wb_all if state_mgr.is_temporarily_mastered(q.id)]

    wb_c1, wb_c2, wb_c3 = st.columns(3)
    with wb_c1: st.metric("🎯 待练错题", f"{len(wb_active_all)} 题")
    with wb_c2: st.metric("🔥 顽固错题", f"{len(wb_stubborn)} 题")
    with wb_c3: st.metric("🏆 历史错题", f"{len(wb_history)} 题")

    if not wb_all:
        st.info("还没有标记任何错题。去『🏷️ 题库逐题标错』把做错的题录进来，这里就能看到全科错题清单。")
    else:
        # 导出错题本备份
        st.download_button(
            "📥 导出错题本备份 (JSON)",
            data=state_mgr.export_wrong_questions_json().encode("utf-8"),
            file_name=f"错题本_{current_subject.value}.json",
            mime="application/json",
            key=f"wb_export_{current_subject.value}",
        )

        # 筛选行:书籍 / 状态 / 章节 / 题型
        f0, f1, f2, f3 = st.columns(4)
        with f0:
            wb_books = ["全部"] + sorted({getattr(q, "book", "880") for q in wb_all})
            wb_book_pick = st.selectbox("书籍", wb_books, index=0, key=f"wb_book_{current_subject.value}")
        with f1:
            wb_status_pick = st.selectbox(
                "状态", ["全部", "🎯 待练", "🔥 顽固", "🏆 历史"], index=0,
                key=f"wb_status_{current_subject.value}")
        with f2:
            wb_chs = ["全部"] + sorted({q.chapter for q in wb_all}, key=lambda c: parse_chapter_number(c))
            wb_ch_pick = st.selectbox("章节", wb_chs, index=0, key=f"wb_ch_{current_subject.value}")
        with f3:
            wb_type_pick = st.selectbox(
                "题型", ["全部", "选择题", "填空题", "解答题"], index=0,
                key=f"wb_type_{current_subject.value}")

        _STATUS_LABEL = {
            "active": ('🎯 待练错题', 'badge-adv'),
            "stubborn": ('🎯 待练 · 🔥 顽固', 'badge-wrong'),  # 顽固也是待练的一种
            "history": ('🏆 历史错题', 'badge-comp'),
        }

        # 应用筛选(待练=全部活跃含顽固;顽固=活跃且做错≥2 —— 与概览重叠语义一致)
        def _wb_pass(q) -> bool:
            if wb_book_pick != "全部" and getattr(q, "book", "880") != wb_book_pick:
                return False
            active = state_mgr.is_in_active_pool(q.id)
            if wb_status_pick == "🎯 待练" and not active:
                return False
            if wb_status_pick == "🔥 顽固" and not (active and state_mgr.get_wrong_count(q.id) >= 2):
                return False
            if wb_status_pick == "🏆 历史" and not state_mgr.is_temporarily_mastered(q.id):
                return False
            if wb_ch_pick != "全部" and q.chapter != wb_ch_pick:
                return False
            if wb_type_pick != "全部" and q.question_type.value != wb_type_pick:
                return False
            return True

        shown = sorted([q for q in wb_all if _wb_pass(q)], key=lambda q: parse_qid_tuple(q.id))
        st.caption(f"共 {len(shown)} 道（按题号排序）")

        # 错题本导出 PDF：导出【当前筛选结果】(书籍/状态/章节/题型 都跟着生效)。
        # 与组卷页同理：download_button 的 data= 是急切求值,若直接放会让每次重跑都触发
        # 无头浏览器排版(十几秒)并堵住整页,故先点「生成」再出下载按钮。
        if shown:
            _wb_ids = tuple(q.id for q in shown)
            _wb_sig = hashlib.md5("|".join(_wb_ids).encode("utf-8")).hexdigest()[:8]
            _WB_EDITIONS = {
                "📝 A4 做题本（留白重做）": (PDFEdition.WORKBOOK_A4, "A4做题本"),
                "📑 详细解析版（带答案解析）": (PDFEdition.SOLUTION, "详细解析"),
                "📄 真题版（1:1 卡片）": (PDFEdition.REAL_EXAM, "真题版"),
            }
            wp1, wp2, wp3 = st.columns([1.8, 1.3, 2.1])
            with wp1:
                _wb_ed_label = st.selectbox(
                    "错题本 PDF 版式", list(_WB_EDITIONS), index=0,
                    key=f"wb_pdf_ed_{current_subject.value}",
                )
            _wb_ed, _wb_ed_short = _WB_EDITIONS[_wb_ed_label]
            _wb_ready_key = f"wb_pdf_ready_{current_subject.value}_{_wb_sig}_{_wb_ed.value}"
            with wp2:
                if not st.session_state.get(_wb_ready_key):
                    if st.button(
                        "📦 生成错题本 PDF", type="primary", use_container_width=True,
                        key=f"wb_pdf_gen_{current_subject.value}_{_wb_sig}_{_wb_ed.value}",
                    ):
                        st.session_state[_wb_ready_key] = True
                        st.rerun()
                else:
                    # 详细解析版：点击下载时才触发 AI 名师补全缺失答案解析
                    _wb_missing_now = [q for q in shown if not (q.answer and q.solution)]
                    _wb_ai_ready_key = f"wb_ai_ready_{current_subject.value}_{_wb_sig}_{_wb_ed.value}"
                    if (_wb_ed == PDFEdition.SOLUTION) and user_api_key and _wb_missing_now and not st.session_state.get(_wb_ai_ready_key):
                        if st.button(
                            "📥 下载详细解析版 PDF（AI 补全解析）",
                            use_container_width=True,
                            key=f"wb_sol_ai_gen_{current_subject.value}_{_wb_sig}_{_wb_ed.value}",
                            help="点击后 AI 名师先补齐缺失的答案解析（有缓存直接复用），完成后自动出下载按钮。",
                        ):
                            _wb_ai2 = _build_solution_tutor(user_api_key, user_api_url, user_model_name)
                            _bar_wb2 = st.progress(0.0, text=f"AI 名师正在生成 {len(_wb_missing_now)} 道错题的答案解析...")
                            def _cb_wb2(done, total, qid, status):
                                if status == "generated":
                                    _bar_wb2.progress(done / total, text=f"AI 名师正在生成答案解析 ({done}/{total})：{qid}")
                                elif status == "cached":
                                    _bar_wb2.progress(done / total, text=f"复用已有 AI 解析缓存 ({done}/{total})...")
                                elif isinstance(status, str) and status.startswith("pdf_detail:"):
                                    _bar_wb2.progress(done / total, text=f"AI 解题失败，正在扫描解析 PDF 定位答案 ({done}/{total})：{qid}（{status[12:]}）")
                                elif status == "pdf":
                                    _bar_wb2.progress(done / total, text=f"AI 解题失败，正在扫描解析 PDF 定位答案 ({done}/{total})：{qid}")
                                elif status == "reviewing":
                                    _bar_wb2.progress(done / total, text=f"AI 阅卷专家正在独立审核答案 ({done}/{total})：{qid}")
                                elif status == "failed":
                                    _bar_wb2.progress(done / total, text=f"AI 解题失败且扫描版未命中 ({done}/{total})：{qid}")
                                elif status == "review_failed":
                                    _bar_wb2.progress(done / total, text=f"AI 审核失败，已标记未审核 ({done}/{total})：{qid}")
                            try:
                                gen_wb2, _ = ensure_solutions(shown, _wb_ai2, progress_cb=_cb_wb2)
                                if gen_wb2 > 0:
                                    get_cached_pdf.clear()
                            except Exception:
                                pass
                            st.session_state[_wb_ai_ready_key] = True
                            st.rerun()
                    else:
                        _pdf_wb2 = _safe_pdf_bytes(
                            f"错题本_{current_subject.value}_{_wb_sig}",
                            f"错题本 · {current_subject.value} · 共 {len(shown)} 题",
                            _wb_ids,
                            edition_str=_wb_ed.value,
                            subject_str=current_subject.value,
                        )
                        if _pdf_wb2:
                            st.download_button(
                                "📥 下载错题本 PDF",
                                data=_pdf_wb2,
                                file_name=f"错题本_{current_subject.value}_{len(shown)}题_{_wb_ed_short}.pdf",
                                mime="application/pdf",
                                use_container_width=True,
                                key=f"wb_pdf_dl_{current_subject.value}_{_wb_sig}_{_wb_ed.value}",
                            )
            with wp3:
                st.caption("💡 生成约十几秒")

        @st.fragment()
        def render_wrongbook_cards():
            _wb_cur = tuple(q for q in shown if state_mgr.is_in_active_pool(q.id) or state_mgr.is_temporarily_mastered(q.id))
            if not _wb_cur:
                st.info("当前筛选下没有错题。")
            for q in _wb_cur:
                stt = _wb_status(q.id)
                w_cnt = state_mgr.get_wrong_count(q.id)
                label, _ = _STATUS_LABEL.get(stt, ('', ''))
                # 摘要标题:一眼看清"哪几道" + 状态 + 做错次数
                head = f"{label}　{q.id}　·　{q.chapter}　·　[{q.difficulty.value}]　·　做错 {w_cnt} 次"
                with st.expander(head):
                    render_stem(q.stem)
                    if q.options:
                        oc1, oc2 = st.columns(2)
                        for oi, opt in enumerate(q.options):
                            (oc1 if oi % 2 == 0 else oc2).markdown(opt)
                    if q.answer:
                        st.markdown(f"**【参考答案】**：`{q.answer}`")
                    if q.solution:
                        st.markdown(f"**【详细解析】**：\n{q.solution}")
                    # 操作按钮(按状态给对应动作)
                    b1, b2, b3, b4 = st.columns(4)
                    if stt in ("active", "stubborn"):
                        with b1:
                            st.button("🏆 归为历史", key=f"wb_arch_{q.id}", use_container_width=True,
                                      help="已掌握，移出待练池、归档为历史错题", on_click=cb_archive_to_history, args=(q.id,))
                        with b2:
                            st.button("➕ 又错一次", key=f"wb_inc_{q.id}", use_container_width=True,
                                      help="再次做错，做错次数+1", on_click=cb_inc_wrong, args=(q.id,))
                    elif stt == "history":
                        with b1:
                            st.button("🎯 放回待练", key=f"wb_react_{q.id}", type="primary", use_container_width=True,
                                      help="重新放回活跃错题池参与组卷", on_click=cb_reactivate_wrong, args=(q.id,))
                    with b4:
                        st.button("🗑️ 删除", key=f"wb_del_{q.id}", use_container_width=True,
                                  help="彻底从错题记录中移除", on_click=cb_remove_wrong, args=(q.id,))



        render_wrongbook_cards()
# -------------------------------------------------------------------------
# WORKSPACE 4: 全科考点覆盖与错题画像 (进度雷达)
# -------------------------------------------------------------------------
elif active_module == "📈 全科考点雷达":
    st.markdown(f"### 📈 {current_books_str} · {current_subject.value} 考点覆盖与错题画像")

    cov_chapters = state_mgr.historical_covered_chapters & default_target_chapters
    total_target_n = max(1, len(default_target_chapters))
    cov_ratio_val = len(cov_chapters) / total_target_n

    # Top Metrics Grid
    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
    with c_m1: st.metric("🎯 待练错题", f"{subject_wrong_count} 题")
    with c_m2: st.metric("🏆 历史错题", f"{past_wrong_count} 题")
    with c_m3: st.metric("🔥 顽固错题", f"{repeated_wrong_count} 题")
    with c_m4: st.metric("📊 考点覆盖率", f"{cov_ratio_val * 100:.1f}%")

    st.markdown(
        f"""
        <div class="glass-card" style="background:linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%); border:1px solid #bbf7d0; margin-top:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:15px; font-weight:800; color:#166534;">🎯 章节覆盖进度</span>
                <span style="font-size:16px; font-weight:900; color:#059669;">{cov_ratio_val * 100:.1f}% · {len(cov_chapters)} / {total_target_n} 章节</span>
            </div>
        """,
        unsafe_allow_html=True,
    )
    st.progress(min(1.0, cov_ratio_val))

    # Badge Pill Matrix
    p_html = []
    for ch in loaded_chapters:
        is_c = ch in cov_chapters
        ch_wrong_n = sum(1 for q in subject_wrong_pool if q.chapter == ch)
        ch_stubb_n = sum(1 for q in subject_wrong_pool if q.chapter == ch and state_mgr.get_wrong_count(q.id) >= 2)
        cls_name = "radar-pill-done" if is_c else "radar-pill-todo"
        sym = "✓ " if is_c else "○ "
        wrong_str = ""
        if ch_wrong_n > 0:
            stubb_str = f" · 🔥{ch_stubb_n}" if ch_stubb_n > 0 else ""
            wrong_str = f" · 错{ch_wrong_n}{stubb_str}"
        p_html.append(f'<span class="radar-pill {cls_name}">{sym}{ch}{wrong_str}</span>')

    st.markdown("".join(p_html) + "</div>", unsafe_allow_html=True)

    # 顽固错题重点攻坚清单
    stubborn_qs = [q for q in subject_active_wrong_pool if state_mgr.get_wrong_count(q.id) >= 2]
    stubborn_qs.sort(key=lambda q: state_mgr.get_wrong_count(q.id), reverse=True)
    if stubborn_qs:
        with st.expander(f"🔥 本科目共 {len(stubborn_qs)} 道高频顽固错题重点攻坚清单", expanded=True):
            for sq in stubborn_qs:
                cnt = state_mgr.get_wrong_count(sq.id)
                tags_s = " ".join(f"#{t}" for t in sq.tags[:2])
                sq_row_html = (
                    f'<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 10px; margin-bottom:6px; background:#fff1f2; border:1px solid #fda4af; border-radius:6px;">'
                    f'<div>'
                    f'<span style="font-weight:800; color:#be123c; font-family:monospace; margin-right:8px;">{sq.id}</span>'
                    f'<span style="font-size:12px; color:#475569; margin-right:8px;">[{sq.chapter}] [{sq.difficulty.value}]</span>'
                    f'<span style="font-size:11.5px; color:#64748b;">{tags_s}</span>'
                    f'</div>'
                    f'<span class="badge" style="background:#be123c; color:#ffffff; font-weight:800;">做错 {cnt} 次</span>'
                    f'</div>'
                )
                st.markdown(sq_row_html, unsafe_allow_html=True)

    # Category Breakdowns
    s_c1, s_c2, s_c3 = st.columns(3)
    with s_c1:
        st.markdown("##### 📐 高等数学")
        adv_chs = [c for c in loaded_chapters if any(q.chapter == c and q.category == ChapterCategory.ADVANCED_MATH for q in all_questions)]
        adv_cov = [c for c in adv_chs if c in cov_chapters]
        st.markdown(f"**覆盖进度**：`{len(adv_cov)} / {len(adv_chs)}` 章节")
        for ch in adv_chs:
            ch_w = sum(1 for q in subject_wrong_pool if q.chapter == ch)
            ch_st = sum(1 for q in subject_wrong_pool if q.chapter == ch and state_mgr.get_wrong_count(q.id) >= 2)
            st_text = f" · 🔥顽固{ch_st}" if ch_st > 0 else ""
            w_tag = f" <span style='color:#e11d48; font-size:12px;'>[错{ch_w}题{st_text}]</span>" if ch_w > 0 else ""
            st.markdown(f"{'✅' if ch in cov_chapters else '⏳'} {ch}{w_tag}", unsafe_allow_html=True)

    with s_c2:
        st.markdown("##### 🔢 线性代数")
        lin_chs = [c for c in loaded_chapters if any(q.chapter == c and q.category == ChapterCategory.LINEAR_ALGEBRA for q in all_questions)]
        lin_cov = [c for c in lin_chs if c in cov_chapters]
        st.markdown(f"**覆盖进度**：`{len(lin_cov)} / {len(lin_chs)}` 章节")
        for ch in lin_chs:
            ch_w = sum(1 for q in subject_wrong_pool if q.chapter == ch)
            ch_st = sum(1 for q in subject_wrong_pool if q.chapter == ch and state_mgr.get_wrong_count(q.id) >= 2)
            st_text = f" · 🔥顽固{ch_st}" if ch_st > 0 else ""
            w_tag = f" <span style='color:#e11d48; font-size:12px;'>[错{ch_w}题{st_text}]</span>" if ch_w > 0 else ""
            st.markdown(f"{'✅' if ch in cov_chapters else '⏳'} {ch}{w_tag}", unsafe_allow_html=True)

    with s_c3:
        st.markdown("##### 🎲 概率论与数理统计")
        prob_chs = [c for c in loaded_chapters if any(q.chapter == c and q.category == ChapterCategory.PROBABILITY for q in all_questions)]
        if not prob_chs:
            st.info("本科目不考概率统计")
        else:
            prob_cov = [c for c in prob_chs if c in cov_chapters]
            st.markdown(f"**覆盖进度**：`{len(prob_cov)} / {len(prob_chs)}` 章节")
            for ch in prob_chs:
                ch_w = sum(1 for q in subject_wrong_pool if q.chapter == ch)
                ch_st = sum(1 for q in subject_wrong_pool if q.chapter == ch and state_mgr.get_wrong_count(q.id) >= 2)
                st_text = f" · 🔥顽固{ch_st}" if ch_st > 0 else ""
                w_tag = f" <span style='color:#e11d48; font-size:12px;'>[错{ch_w}题{st_text}]</span>" if ch_w > 0 else ""
                st.markdown(f"{'✅' if ch in cov_chapters else '⏳'} {ch}{w_tag}", unsafe_allow_html=True)


# =========================================================================

# -------------------------------------------------------------------------
# WORKSPACE 5: 每日错题（艾宾浩斯抗遗忘独立模块）
# -------------------------------------------------------------------------
elif active_module == "📅 每日错题":
    st.markdown("### 📅 每日错题 · 艾宾浩斯抗遗忘")
    st.caption("到期优先、逾期越久越靠前；做对间隔翻倍、做错隔天回炉。")
    st.markdown("---")
    _q_by_id2 = {q.id: q for q in all_questions}
    _eb_list_key = f"eb_list_{current_subject.value}"
    # 今日已处理集合（做对/又错/没做都计入）：防止重新 select 或"再开 10 道"把当天已处理题拉回
    _eb_processed_key = f"eb_processed_{current_subject.value}"
    _eb_processed = set(state_mgr.get_processed_today()) | set(st.session_state.get(_eb_processed_key, ()))  # 持久化(刷新不失效) + 会话级
    # 今日安排持久化：每天首次进入自动选一批并记录；刷新后恢复同一份安排（减去已处理），不再自动生成新题
    # 考点覆盖：qid -> 章节 映射，选题按章节轮转，10 道尽量覆盖不同考点
    # 考点覆盖：qid -> 章节 映射（无 chapter 的题回退到 qid 前缀作伪章节，防止全部落入空桶稀释覆盖）
    _eb_chapter_map = {qid: (_q_by_id2[qid].chapter or qid.split("-")[0] or "其他") for qid in _q_by_id2}
    _eb_assigned = state_mgr.get_assigned_today()
    if not _eb_assigned:
        _due_qids = state_mgr.select_daily_wrong(target=10, exclude_ids=_eb_processed, chapter_of=_eb_chapter_map)
        _eb_assigned = [qid for qid in _due_qids if qid in _q_by_id2 and qid not in _eb_processed]
        if _eb_assigned:
            state_mgr.mark_assigned_today(_eb_assigned)
    _eb_ids = tuple(q for q in _eb_assigned if q in _q_by_id2 and q not in _eb_processed)
    # 统一题号：按学科分块(高数在前/线代在后),块内按题型分组(选择→填空→解答),
    # 保证做题本/解析版/网页三处题号一致
    def _eb_order(_qid):
        _qq = _q_by_id2[_qid]
        # 学科分块:高数0 → 线代1 → 概率论2 → 未知9(显式分支,避免概率论被else误归线代块)
        if _qq.category == ChapterCategory.ADVANCED_MATH:
            _subj_rank = 0
        elif _qq.category == ChapterCategory.LINEAR_ALGEBRA:
            _subj_rank = 1
        elif _qq.category == ChapterCategory.PROBABILITY:
            _subj_rank = 2
        else:
            _subj_rank = 9
        _type_rank = (QuestionType.CHOICE, QuestionType.FILL_BLANK, QuestionType.SOLUTION).index(_qq.question_type) if _qq.question_type in (QuestionType.CHOICE, QuestionType.FILL_BLANK, QuestionType.SOLUTION) else 9
        return (_subj_rank, _type_rank)
    _eb_ids = tuple(sorted(_eb_ids, key=_eb_order))
    st.session_state[_eb_list_key] = list(_eb_ids)
    # 再开 10 道：显式追加到今日安排（刷新后仍保留），不触发自动重新安排
    _eb_seen = set(_eb_ids)
    if st.button("➕ 今天做完了，再开 10 道", use_container_width=True, key=f"eb_more_{current_subject.value}"):
        _rest = [
            qid for qid, rec in state_mgr.wrong_questions.items()
            if rec.is_active_in_pool and rec.wrong_count > 0
            and qid not in _eb_seen and qid not in _eb_processed and qid in _q_by_id2
        ]
        _rest.sort(
            key=lambda q: (state_mgr.wrong_questions[q].added_at or "", state_mgr.wrong_questions[q].wrong_count),
            reverse=True,
        )
        _extra = _rest[:10]
        if _extra:
            state_mgr.mark_assigned_today(_extra)
            _eb_assigned = state_mgr.get_assigned_today()
            _eb_ids = tuple(q for q in _eb_assigned if q in _q_by_id2 and q not in _eb_processed)
            _eb_ids = tuple(sorted(_eb_ids, key=lambda _qid: (QuestionType.CHOICE, QuestionType.FILL_BLANK, QuestionType.SOLUTION).index(_q_by_id2[_qid].question_type) if _q_by_id2[_qid].question_type in (QuestionType.CHOICE, QuestionType.FILL_BLANK, QuestionType.SOLUTION) else 9))
            st.session_state[_eb_list_key] = list(_eb_ids)
            st.rerun()
        else:
            st.info("错题本里暂时没有更多可练的题目了。")

    if not _eb_ids:
        st.info("今日安排已清空 🎉 点上方『再开 10 道』可继续加练；今天处理过的错题明天会自动排进复习队列。")
    else:
        _eb_qs = [_q_by_id2[qid] for qid in _eb_ids]
        _clr_a, _clr_b = st.columns([4, 1])
        with _clr_b:
            if st.button("🗑️ 清空今日", use_container_width=True, key=f"eb_clear_{current_subject.value}",
                         help="今天剩下的题顺延到明天（明天到期自动再推，刷新不失效）；想接着做请点上方『再开 10 道』。"):
                state_mgr.postpone_review(list(_eb_ids), days=1)
                state_mgr.mark_processed_today(list(_eb_ids))
                _eb_pl = set(st.session_state.get(_eb_processed_key, ()))
                _eb_pl.update(_eb_ids)
                st.session_state[_eb_processed_key] = list(_eb_pl)
                st.session_state[_eb_list_key] = []
                st.rerun()

        # ---- PDF 导出（异步后台生成 + 文件缓存：点按钮秒回，PDF 生成完自动出现下载按钮） ----
        _eb_sig = hashlib.md5("|".join(_eb_ids).encode("utf-8")).hexdigest()[:8]
        _eb_pdf_id = f"今日错题_{current_subject.value}_{_eb_sig}"
        _eb_missing = [q for q in _eb_qs if needs_solution(q)]
        render_eb_pdf_panel(
            sig=_eb_sig,
            qs=_eb_qs,
            paper_id=_eb_pdf_id,
            subject_str=current_subject.value,
            user_api_key=user_api_key,
            missing_qs=_eb_missing,
            ai_ready_key=f"eb_ai_ready_{current_subject.value}_{_eb_sig}",
        )

        # ---- 题目卡片（智能拼好卷风格：题号 + 题干 + 选项 + 反馈按钮） ----
        # fragment 化：点「做对/又错/没做」只局部刷新本卡片区（毫秒级），不再全量 rerun 整脚本
        # （其它 tab / 侧边栏 / URL 同步 / PDF 面板全部跳过）。列表从 session_state 实时读，
        # 处理过的题移除后 fragment 自动重渲染；空列表时显示清空提示。
        @st.fragment()
        def render_eb_cards():
            # 防御：_q_by_id2 是 fragment 闭包捕获的静态题库快照（session 内题库不变），
            # 过滤掉快照中不存在的 id，避免"再开 10 道/切科目"等全量 rerun 路径引入新 id 时 KeyError。
            def _eb_finish(_qid: str, _ok: bool) -> None:
                """on_click 回调：fragment rerun 前先落地状态+今日列表，本次渲染即移除（不用点两次）。"""
                state_mgr.record_review_result(_qid, bool(_ok))
                state_mgr.mark_processed_today([_qid])
                _eb_lst = list(st.session_state.get(_eb_list_key, ()))
                if _qid in _eb_lst:
                    _eb_lst.remove(_qid)
                st.session_state[_eb_list_key] = _eb_lst
                _eb_pl = set(st.session_state.get(_eb_processed_key, ()))
                _eb_pl.add(_qid)
                st.session_state[_eb_processed_key] = list(_eb_pl)

            def _eb_skip(_qid: str) -> None:
                """没做：今天不再推，明天继续推。"""
                state_mgr.mark_wrong_not_done(_qid)
                state_mgr.mark_processed_today([_qid])
                _eb_lst = list(st.session_state.get(_eb_list_key, ()))
                if _qid in _eb_lst:
                    _eb_lst.remove(_qid)
                st.session_state[_eb_list_key] = _eb_lst
                _eb_pl = set(st.session_state.get(_eb_processed_key, ()))
                _eb_pl.add(_qid)
                st.session_state[_eb_processed_key] = list(_eb_pl)

            _ids = tuple(qid for qid in st.session_state.get(_eb_list_key, ()) if qid in _q_by_id2)
            if not _ids:
                st.info("今日安排已清空 🎉 点上方『再开 10 道』可继续加练；今天处理过的错题明天会自动排进复习队列。")
                return
            st.caption(f"今日安排 {len(_ids)} 道：到期优先、逾期越久越靠前。")
            for _i2, _qid2 in enumerate(_ids, 1):
                _q2 = _q_by_id2[_qid2]
                _r2 = state_mgr.wrong_questions.get(_qid2)
                with st.container(border=True):
                    _head2 = (
                        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">'
                        f'<span style="font-weight:800;font-size:15px;">{_i2}.</span>'
                        f'<span style="font-size:11px;color:#64748b;font-family:monospace;">{_qid2} · 错{_r2.wrong_count if _r2 else 1}次 · 阶段{_r2.review_stage if _r2 else 0}/5 · {_r2.error_tag if _r2 else "概念模糊"}</span>'
                        f'</div>'
                    )
                    st.markdown(_head2, unsafe_allow_html=True)
                    render_stem(_q2.stem)
                    if _q2.options:
                        _oc1, _oc2 = st.columns(2)
                        for _oi, _opt in enumerate(_q2.options):
                            (_oc1 if _oi % 2 == 0 else _oc2).markdown(_opt)
                    with st.expander("📖 查看答案与解析"):
                        if _q2.answer:
                            st.markdown(f"**【参考答案】**：`{_q2.answer}`")
                        if _q2.solution:
                            st.markdown(f"**【详细解析】**：\\n{_q2.solution}")
                        else:
                            st.caption("（暂无解析，下载详细解析版 PDF 时由 AI 名师补全）")
                    _ec1, _ec2, _ec3 = st.columns(3)
                    with _ec1:
                        st.button("✅ 做对了", key=f"eb_ok_{_qid2}", use_container_width=True,
                                  on_click=_eb_finish, args=(_qid2, True))
                    with _ec2:
                        st.button("❌ 又错了", key=f"eb_no_{_qid2}", use_container_width=True,
                                  on_click=_eb_finish, args=(_qid2, False))
                    with _ec3:
                        st.button("⏭️ 没做", key=f"eb_skip_{_qid2}", use_container_width=True,
                                  help="今天没做这道题：今天不再推，明天会继续推给你。",
                                  on_click=_eb_skip, args=(_qid2,))

        render_eb_cards()

# 7. URL 错题码同步（把当前科目错题状态写回网址，保持链接可跨设备恢复）
# =========================================================================
if url_data_key and current_subject != SubjectType.CUSTOM:
    latest_code = state_mgr.to_url_code(canonical_ids)
    # 仅在真正变化时写入，避免无谓 rerun
    if st.query_params.get(url_data_key) != latest_code:
        st.query_params[url_data_key] = latest_code
    # seen 码(已抽过题)写回 n1/n2/n3,与错题码独立
    if url_seen_key:
        seen_code = state_mgr.seen_to_url_code(canonical_ids)
        if st.query_params.get(url_seen_key) != seen_code:
            st.query_params[url_seen_key] = seen_code
    # 真题错题/seen 码写回 z1/zn1 等(锚定真题 canonical,与 880 的 d/n 独立)
    if url_zhenti_key and zhenti_canonical:
        zt_code = state_mgr.to_url_code(zhenti_canonical)
        if st.query_params.get(url_zhenti_key) != zt_code:
            st.query_params[url_zhenti_key] = zt_code
    if url_zhenti_seen_key and zhenti_canonical:
        zt_seen_code = state_mgr.seen_to_url_code(zhenti_canonical)
        if st.query_params.get(url_zhenti_seen_key) != zt_seen_code:
            st.query_params[url_zhenti_seen_key] = zt_seen_code
    if url_1000_key and canonical_1000:
        k_code = state_mgr.to_url_code(canonical_1000)
        if st.query_params.get(url_1000_key) != k_code:
            st.query_params[url_1000_key] = k_code
    if url_1000_seen_key and canonical_1000:
        k_seen_code = state_mgr.seen_to_url_code(canonical_1000)
        if st.query_params.get(url_1000_seen_key) != k_seen_code:
            st.query_params[url_1000_seen_key] = k_seen_code

# 试卷码同步：把当前生成的试卷题号写回网址（q1/q2/q3），做完后可凭链接查阅答案
if paper_url_key and current_subject != SubjectType.CUSTOM:
    _cur_bundle = st.session_state.get("current_bundle_p1")
    _cur_paper = st.session_state.get("current_paper_p1")
    _papers_qids = None
    if _cur_bundle:
        _papers_qids = [[q.id for q in p.questions] for p in _cur_bundle.papers]
    elif _cur_paper:
        _papers_qids = [[q.id for q in _cur_paper.questions]]
    if _papers_qids:
        _pcode = StateManager.encode_papers_code(_papers_qids, canonical_ids)
        if st.query_params.get(paper_url_key) != _pcode:
            st.query_params[paper_url_key] = _pcode

# AI 配置同步：仅 Base URL 与 Model 进网址，API Key 严禁写入
_ai_url = st.session_state.get("ai_base_url", "")
_ai_model = st.session_state.get("ai_model", "")
if _ai_url and st.query_params.get("au") != _ai_url:
    st.query_params["au"] = _ai_url
if _ai_model and st.query_params.get("am") != _ai_model:
    st.query_params["am"] = _ai_model


# =========================================================================
# 8. Academic Footer
# =========================================================================
st.markdown(
    f"""
    <div style="text-align:center; color:#94a3b8; font-size:12px; margin-top:50px; padding:24px 0; border-top:1px solid #e2e8f0;">
        考研拼好卷系统 · 当前书籍: {current_books_str} · 当前科目: {current_subject.value} · {len(loaded_chapters)} 章 · {len(all_questions)} 题
    </div>
    """,
    unsafe_allow_html=True,
)
