"""
考研数学智能组卷系统 - AI 生成答案的独立审核服务
AI 名师生成答案解析后，由"阅卷专家"角色独立重算题目（不看原解答），再逐项对照，
输出 正确 / 有误 / 存疑 结论；有误时给出修正后的完整解答，用于替换原解析。
防止"AI 自说自话"导致的错误答案直接进入 PDF。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SYSTEM_REVIEWER_PROMPT = """你是一位极其严谨的考研数学阅卷专家，负责审核另一位老师给出的解答。
审核时必须遵守以下纪律：
1. 【独立重算】：先抛开待审解答，亲自完整求解这道题（选择、填空也要亲自算），得出你自己的结论。
   重算过程只需在心中完成，不要输出任何推导过程——你的输出只包含审核结论与必要判断依据，不展示求解步骤。
2. 【逐项对照】：再逐条检查待审解答的【最终答案】与【关键推导步骤】，找出任何错误或漏洞，
   包括但不限于：答案算错、跳步、符号错误、漏讨论定义域/连续性/极限方向、公式误用、选项张冠李戴。
3. 【诚实结论】：不要为了迎合而说"正确"；只要发现实质性错误，必须判"有误"。
   若你自己也无法确定正确答案（例如题目条件有歧义），判"存疑"，绝不含糊。
4. 【不确定就不改（最高优先级）】：只有当你能 100% 确认原解答错误、且你的修正推导无任何疑问时，才判"有误"并给出修正版。
   任何情况下，只要你对自己重算的结论没有十足把握——包括题目表述有歧义、教材定义有争议（如"无穷大"是否包含负无穷大）、
   两种理解都能说得通、或你觉得"应该是这样但不敢肯定"——一律判"存疑"，禁止输出修正版。
   宁可存疑，不可错改。
6. 【题干条件核对（最高优先级）】：审核时必须把待审解答用到的每个条件与【题干正文】逐字核对，严禁出现"按自洽性理解""按选项自洽""理解为"等擅自改写题干条件的做法。若待审解答把题干条件改写成另一形式（如把"不能由某矩阵的行向量线性表示"改成"列向量线性表示"），无论其推理多么自洽，一律判"有误"并给出基于题干原条件的修正版；若某选项恰是题干条件的直接翻译（增广秩 +1 等）而待审解答反而改题回避它，必须在判卷说明中明确指出。
5. 【检查解析是否自洽定稿】：在给出结论前，先检查待审解答全文是否自洽：
   是否出现多个互相矛盾的【答案】？是否含自我纠错/自我怀疑痕迹（"等等""重新检查""需要再次确认""应为X而不是Y"
   "不对，应该是""更正"等）？推导中间是否出现与最终答案矛盾的结论？
   若存在任一情况 → 判"存疑"，判卷说明中写"待审解答前后不自洽（先给X后改Y），需重新生成定稿版"，禁止判"正确"。

输出格式（严格遵守，每行一个字段）：
【审核结论】：只输出三个固定词之一——正确 / 有误 / 存疑，禁止输出"基本正确""大体正确""不完全正确"等其他表述。
【判卷说明】：至多 1-2 句，简明扼要地说明判卷依据；若正确指出关键得分点，若有误明确指出错在何处，若存疑说明你的疑问点。
【标准答案】：你独立求解得到的最终答案（选择题给选项字母，填空题给确切表达式，解答题给最终结果）。若判存疑且你无法确定，写"无法确定"。
【修正后完整解析】：仅当判"有误"时输出修正后的完整解答（LaTeX，公式用 $...$ 或 $$...$$），步骤必须完整、可直接照学，但要精炼——不要出现试错、反复、自我怀疑的内容；判"存疑"或"正确"时，只输出"无需修正"。

总约束：判"正确"或"存疑"时，不要输出任何解题推导过程（重算只在心中完成），所有字段精简；仅判"有误"时才输出修正解析，且修正解析步骤完整但精炼，不得包含试错过程。
"""


@lru_cache(maxsize=4)
def _get_review_tutor_cached(api_key: str, base_url: str, model: str):
    """按审核三要素构造审核 tutor（审核配置未变则复用实例）"""
    from core.ai_tutor import AITutor
    return AITutor(api_key=api_key, base_url=base_url, model=model)


def _get_review_tutor(fallback):
    """优先加载独立审核模型（用户配置的 DeepSeek/Claude 中转）；未配置时回退为生成同模型。

    配置内容作为缓存键：运行期配置未变则复用同一 AITutor 实例，避免每题读盘重建。
    """
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "user_data" / "ai_config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if cfg.get("review_api_key") and cfg.get("review_base_url"):
            model = cfg.get("review_model")
            if not model:
                # 二级兜底：未显式配置模型时按端点推断（deepseek→deepseek-chat，其余→Claude），
                # 防止"配了 Claude 中转但忘填 model"导致 deepseek-chat 请求 404
                model = (
                    "deepseek-chat"
                    if "deepseek" in cfg["review_base_url"].lower()
                    else "claude-sonnet-4-5-20250929"
                )
            return _get_review_tutor_cached(
                cfg["review_api_key"],
                cfg["review_base_url"],
                model,
            )
    except Exception:
        pass
    return fallback


def review_solution(question, generated_text: str, ai_tutor) -> dict:
    """审核 AI 名师生成的解答（默认用独立审核模型，未配置时同模型审核）。

    Args:
        question: QuestionItem
        generated_text: AI 名师生成的原解答全文
        ai_tutor: AITutor 实例（生成模型；无独立审核配置时作为回退）

    Returns:
        {"verdict": "verified"|"fixed"|"doubt"|"unknown",
         "corrected_text": 修正后的完整解答（verdict=fixed 时存在）,
         "raw": 审核响应原文}
    """
    reviewer = _get_review_tutor(ai_tutor)
    if reviewer is None:
        # 无独立审核配置且无回退 tutor → 无法审核，返回 unknown（不崩溃）
        return {"verdict": "unknown", "corrected_text": "", "raw": ""}

    options_text = ""
    if question.options:
        options_text = "\n选项：\n" + "\n".join(question.options)

    user_content = f"""【题目编号】：{question.id}
【所属章节】：{question.chapter} ({question.category.value})
【题型难度】：{question.difficulty.value} - {question.question_type.value}
【题干正文】：
{question.stem}{options_text}

【待审解答（另一位老师给出的）】：
{generated_text}

请严格按阅卷要求独立重算并对照，然后输出结论。"""

    try:
        raw = reviewer.chat_complete(SYSTEM_REVIEWER_PROMPT, user_content, temperature=0.1)
    except Exception:
        raw = ""
    if not raw or "【审核结论】" not in raw:
        return {"verdict": "unknown", "corrected_text": "", "raw": raw}

    m = re.search(r"【审核结论】\s*[:：]?\s*([^\n【]{1,40})", raw)
    verdict_raw = m.group(1).strip() if m else ""
    v = verdict_raw
    # 注意顺序：先排除"不正确"，防止子串误判为"正确"
    if any(x in v for x in ("不正确", "不认为正确", "并非正确", "不够正确")):
        verdict = "fixed"
    elif any(x in v for x in ("有误", "错误", "算错", "计算有误")):
        verdict = "fixed"
    elif any(x in v for x in ("存疑", "不确定", "无法确定", "难以判断")):
        verdict = "doubt"
    elif "正确" in v and not any(x in v for x in ("不正确", "不认为正确", "并非正确", "不够正确", "但", "不过", "基本", "大体", "不完全", "略", "跳步", "瑕疵", "小问题", "存疑", "与否", "不能确认", "无法保证")):
        # 明确肯定：正确（含"正确 答案无误/完全正确/解答正确/结论正确"等常见后缀）→ verified；
        # 含转折/模糊词（"正确，但推导有跳步""基本正确"）→ 落入下一分支 doubt（保守）。
        # 注："不正确/不够正确"已由上方 fixed 分支先拦截；"存疑"已由 doubt 分支先拦截，此处双保险
        verdict = "verified"
    elif "正确" in v:
        # 模糊肯定（"基本正确/大体正确"等）→ 保守降级存疑，走视觉核验
        verdict = "doubt"
    else:
        verdict = "unknown"

    corrected_text = ""
    if verdict == "fixed":
        m2 = re.search(
            r"【修正后完整解析】\s*[:：]?\s*\n*([\s\S]+?)(?=\n【(?:审核结论|判卷说明|标准答案|修正后完整解析)】\s*[:：]?|$)",
            raw,
        )
        if m2:
            corrected_text = m2.group(1).strip()
            if corrected_text.startswith("无需修正") or corrected_text == "无需修正":
                corrected_text = ""
        if not corrected_text:
            # 判"有误"但拿不到修正内容：降级为存疑，保留原解答，由上层走视觉核验/标记
            verdict = "doubt"

    # 契约：仅 fixed 携带修正内容
    if verdict != "fixed":
        corrected_text = ""

    # 自洽性双保险（第八轮）：解析含自我纠错痕迹或多个矛盾答案 → 无论审核模型如何判，降级存疑
    # （AI 生成"先给X后改Y"时，审核模型可能只看到最终答案而判"正确"，但正文不可信）
    try:
        from core.ai_solutions import _has_self_correction, _multi_answer_conflict
    except Exception:
        def _has_self_correction(t): return False
        def _multi_answer_conflict(t): return False

    # 生成侧诚实存疑（两模型第四轮审查确认：必须检查 generated_text 而非修正版；
    # 词表与 split/缓存判定的存疑词保持一致，覆盖 无法确定/不确定/存疑/暂无/难以判断）
    _UNCERTAIN_KW = ("无法确定", "不确定", "存疑", "暂无", "难以判断")
    if verdict in ("verified", "fixed"):
        gen_uncertain = any(x in generated_text for x in _UNCERTAIN_KW)
        if gen_uncertain and verdict == "verified":
            # 生成侧存疑 + 审核判"正确"：没有确定的答案可审核，降级存疑
            verdict = "doubt"
            corrected_text = ""
        elif gen_uncertain and verdict == "fixed":
            # 生成侧存疑 + 审核给修正：仅当修正版自身不含存疑词（审核确实给出确定答案）才保留；
            # 修正版仍含存疑词说明审核也没把握 → 降级存疑，避免把审核的"无法确定"当修正采用
            if any(x in corrected_text for x in _UNCERTAIN_KW):
                verdict = "doubt"
                corrected_text = ""

    # 自洽性降级（第八轮）：生成原文含纠错痕迹/矛盾答案 → verified 一律降级 doubt；
    # fixed 时若修正版干净则保留（修正版已替换原文），修正版自身仍含痕迹 → 降级 doubt
    _is_choice = False
    try:
        from core.ai_solutions import _is_choice_type
        _is_choice = _is_choice_type(question)
    except Exception:
        pass
    if verdict == "verified" and (_has_self_correction(generated_text) or _multi_answer_conflict(generated_text, _is_choice)):
        verdict = "doubt"
        corrected_text = ""
    elif verdict == "fixed" and corrected_text:
        if _has_self_correction(corrected_text) or _multi_answer_conflict(corrected_text, _is_choice):
            verdict = "doubt"
            corrected_text = ""

    return {"verdict": verdict, "corrected_text": corrected_text, "raw": raw}
