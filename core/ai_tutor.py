"""
考研数学智能组卷系统 - 通用大模型 AI 智能名师答疑服务 (Clean-Room 原创实现)
支持任意兼容 OpenAI 接口规范的大模型（DeepSeek、GPT-4o、Qwen、GLM、Claude中转、Ollama 本地大模型等）
"""
from __future__ import annotations

import json
import logging
import os
import re
import random
import time
import urllib.error
import urllib.request
from typing import Generator

from core.models import QuestionItem

logger = logging.getLogger(__name__)

SYSTEM_TUTOR_PROMPT = """你是一位顶尖考研数学名师，精通高等数学、线性代数与概率论全部题型与考场解法。
请为学生详细解答指定的考研数学《880》题目。

解答要求：
1. 【核心考点】：清晰点明本题考查的核心定理或解题方法。
2. 【标准答案】：明确给出最终正确答案（选择题给出选项字母如【答案：C】，填空题给出确切表达式，解答题给出最终结果）。
3. 【详细推导步骤】：逻辑严密清晰，分步书写，所有数学公式必须使用标准的 LaTeX 语法（行内公式用 $...$，独立公式用 $$...$$）。
4. 【避坑关键提醒】：结合历年考场实战经验，一针见血指出学生最容易失分、写错或忽略的隐蔽陷阱与易错点。
5. 【诚实优先（最高优先级）】：绝不为了凑答案而瞎编。若你对这道题没有十足把握、或题目本身表述有歧义、定义有争议（如"无穷大"是否包含负无穷大）、
   或你只能给猜测性结论时，必须在【标准答案】中如实写"无法确定"，并在【详细推导步骤】中说明你卡住/存疑的原因和你的分析过程，
   严禁虚构一个看似合理的答案或推导。宁可不答，不可错答。只有当你确认答案无误时，才给出确定的【标准答案】。
8. 【题干神圣不可篡改（最高优先级）】：题干给出的每一个条件都必须原样使用，严禁为了凑选项而改写、弱化、强化或"按自洽性理解"地修改题干表述（例如把"不能由某矩阵的行向量线性表示"改成"列向量线性表示"）。若某选项恰好是题干条件经标准推理的直接翻译（如"某行向量不能由某矩阵的行向量线性表示"⟺"该矩阵下方加一行后秩 +1"），则该选项就是正确答案；不要因为"它显得太直接"或"与单选不矛盾"而怀疑题干并擅自改题。若题干确实有歧义，应在【标准答案】中注明你的理解与歧义点（按诚实条款），绝不静默替换条件。
7. 【答案区块唯一性（最高优先级）】：全部推理、检查、验证必须在你的内部思考中完成，输出的正文必须是最终定稿。
   严禁出现任何自我纠错、自我怀疑、重新检查、前后矛盾的内容——禁止出现"等等""重新检查""需要再次确认""应为X而不是Y"
   "不对，应该是""更正""抱歉""写错了""刚才""我们换个方法再验证""不过需要再次确认"等任何纠错过程句式。
   全文只允许出现一次最终【标准答案】（选择题只给一个选项字母），【答案】、推导中间结论、最终结论必须完全自洽。
   若你在内部检查中发现最初思路有误，就在内部修正后直接输出正确的干净版本，绝不把"先得到X、后来发现应为Y"的过程写进正文。
   若你最终仍不能确定答案，按第 5 条诚实条款如实输出"无法确定"。
7. 【答案区块唯一性（最高优先级）】：全文所有答案区块（【标准答案】【答案】【最终答案】【参考答案】）内容必须完全一致，
   只能出现一个最终答案。若你同时使用【标准答案】与【最终答案】两个小节，二者必须相同。
   严禁"最终结果不要写成X""正确答案是Y（而正文前面写的是X）"这类补救式写法——
   一旦发现前面写错，直接回头改写前面的内容使全文连贯，绝不追加纠正段。
   定稿正文中不得出现任何与最终答案矛盾的中间结论（如先判断"发散"、后纠正为具体数值）。
"""


# ===== 本地成本日志（0 API 开销；仅统计非流式 chat_complete 的 usage）=====
import threading as _threading
import datetime as _datetime
from pathlib import Path as _Path

_COST_LOCK = _threading.Lock()
_COST_MAX = 20000


def _cost_log_path() -> _Path:
    return _Path(__file__).resolve().parent.parent / "user_data" / "ai_cost_log.json"


def _log_cost(model: str, prompt_tokens: int, completion_tokens: int, ok: bool) -> None:
    """记录一次 API 调用的 token 用量（本地 JSON，供侧边栏展示消耗）"""
    try:
        with _COST_LOCK:
            p = _cost_log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
                if not isinstance(data, list):
                    data = []
            except Exception:
                data = []
            _m = (model or "").lower()
            tag = "solution" if "claude" in _m else ("review" if "deepseek" in _m else "chat")
            data.append({
                "ts": _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "model": model,
                "tag": tag,
                "pt": int(prompt_tokens or 0),
                "ct": int(completion_tokens or 0),
                "ok": bool(ok),
            })
            if len(data) > _COST_MAX:
                data = data[-_COST_MAX:]
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
    except Exception:
        pass



SYSTEM_TUTOR_PROMPT_FINAL = SYSTEM_TUTOR_PROMPT + """

【定稿输出要求（本次必须执行）】：本次输出将作为最终版本直接展示给学生。
请在内部完成全部推理与检查后直接输出最终定稿：正文只出现一个最终【标准答案】，推导与答案完全自洽，
严禁任何自我纠错过程痕迹（"等等""重新检查""应为X而不是Y""更正"等）。若你确实无法确定答案，直接如实写"无法确定"。"""


class AITutor:
    """通用大模型 AI 智能解题助手（支持任意 OpenAI 兼容端点）"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "deepseek-chat",
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.model = model or "deepseek-chat"
        self.endpoint = self._normalize_endpoint(base_url or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"))

    @staticmethod
    def _normalize_endpoint(url: str) -> str:
        url = (url or "").strip().rstrip("/")
        if not url:
            return "https://api.deepseek.com/chat/completions"
        if url.endswith("/chat/completions"):
            return url
        if not re.search(r"/v\d+[a-z]*$", url):
            # 裸域名 / 缺版本前缀：统一补 /v1（OpenAI 兼容中转站惯例）
            url = f"{url}/v1"
        return f"{url}/chat/completions"

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
        """重试退避：优先 Retry-After 响应头；否则 1.5s/3s/6s 指数 + 随机抖动（防并发重试撞车）"""
        if retry_after:
            try:
                return min(max(float(retry_after), 0.5), 30.0)
            except Exception:
                pass
        return (2 ** attempt) * 1.5 + random.uniform(0, 0.5)

    def solve_question_stream(self, question: QuestionItem) -> Generator[str, None, None]:
        if not self.api_key:
            yield "【提示】未配置 API Key，请在侧边栏填写您的 Base URL、API Key 与 Model 名称开启 AI 智能精讲。"
            return

        options_text = ""
        if question.options:
            options_text = "\n选项：\n" + "\n".join(question.options)

        user_content = f"""【题目编号】：{question.id}
【所属章节】：{question.chapter} ({question.category.value})
【题型难度】：{question.difficulty.value} - {question.question_type.value}
【考查标签】：{", ".join(question.tags) if question.tags else "常规考点"}
【题干正文】：
{question.stem}{options_text}

请按照名师要求提供规范的解题步骤、最终答案与避坑指南。"""

        yield from self.chat_stream(SYSTEM_TUTOR_PROMPT, user_content, temperature=0.2)

    def solve_question(self, question: QuestionItem, final_draft: bool = False) -> str:
        """非流式解题：一次返回完整解析（用于 Claude 中转等流式不可用的端点）。

        与 solve_question_stream 使用相同的题目上下文与提示词，仅调用方式不同。
        final_draft: True 时使用定稿强化提示词（上一版含自我纠错痕迹时打回重试）。
        """
        if not self.api_key:
            return "【提示】未配置 API Key，请在侧边栏填写您的 Base URL、API Key 与 Model 名称开启 AI 智能精讲。"

        options_text = ""
        if question.options:
            options_text = "\n选项：\n" + "\n".join(question.options)

        user_content = f"""【题目编号】：{question.id}
【所属章节】：{question.chapter} ({question.category.value})
【题型难度】：{question.difficulty.value} - {question.question_type.value}
【考查标签】：{", ".join(question.tags) if question.tags else "常规考点"}
【题干正文】：
{question.stem}{options_text}

请按照名师要求提供规范的解题步骤、最终答案与避坑指南。"""

        return self.chat_complete(
            SYSTEM_TUTOR_PROMPT_FINAL if final_draft else SYSTEM_TUTOR_PROMPT,
            user_content,
            temperature=0.2,
        )

    def chat_complete(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.1,
        timeout: int = 300,
    ) -> str:
        """非流式对话：一次返回完整文本（用于独立审核等不需要流式展示的场景）。

        兼容 OpenAI /chat/completions 与 Claude 中转站的 OpenAI 兼容端点。
        """
        if not self.api_key:
            return "【提示】未配置 API Key，请在侧边栏填写您的 Base URL、API Key 与 Model 名称开启 AI 智能精讲。"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept-Encoding": "identity",
        }
        # 指数退避重试（最多 3 次重试 + 1 次初始调用）：仅重试可恢复错误
        # 429/5xx/网络异常/超时/空响应/JSON 解析失败；业务/鉴权错误(4xx)不重试
        _retryable_http = {429, 500, 502, 503, 504, 529}
        _last_err = ""
        for _attempt in range(4):
            try:
                req = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8")
                result = json.loads(body)
                content = result["choices"][0]["message"]["content"] or ""
                if not content.strip():
                    # 空响应（部分成功/截断）→ 视为可重试
                    _last_err = "empty response"
                    if _attempt < 3:
                        time.sleep(self._retry_delay(_attempt))
                        continue
                else:
                    _usage = result.get("usage") or {}
                    _log_cost(self.model, int(_usage.get("prompt_tokens") or 0), int(_usage.get("completion_tokens") or 0), True)
                    return content
            except urllib.error.HTTPError as he:
                err_body = ""
                try:
                    err_body = he.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                if he.code in _retryable_http and _attempt < 3:
                    _last_err = f"HTTP {he.code}"
                    print(f"[AITutor] 重试 {_attempt+1}/3（{_last_err}） endpoint={self.endpoint} model={self.model}", flush=True)
                    time.sleep(self._retry_delay(_attempt, he.headers.get("Retry-After") if hasattr(he, "headers") else None))
                    continue
                _log_cost(self.model, 0, 0, False)
                return f"【API 错误】HTTP {he.code}: {he.reason}\n请求地址: `{self.endpoint}`\n模型: `{self.model}`\n{err_body}"
            except Exception as e:
                _last_err = f"{type(e).__name__}: {e}"
                if _attempt < 3:
                    print(f"[AITutor] 重试 {_attempt+1}/3（{_last_err}） endpoint={self.endpoint} model={self.model}", flush=True)
                    time.sleep(self._retry_delay(_attempt))
                    continue
                _log_cost(self.model, 0, 0, False)
                return f"【网络异常】无法连接至大模型服务 `{self.endpoint}` ({self.model}): {_last_err}"
        _log_cost(self.model, 0, 0, False)
        return f"【网络异常】重试 3 次仍失败（{_last_err}） endpoint=`{self.endpoint}` model=`{self.model}`"

    def chat_stream(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.2,
    ) -> Generator[str, None, None]:
        """通用流式对话（自定义 system prompt）：供 AI 名师解题、独立审核、二次求解等复用。"""
        if not self.api_key:
            yield "【提示】未配置 API Key，请在侧边栏填写您的 Base URL、API Key 与 Model 名称开启 AI 智能精讲。"
            return

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "stream": True,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
        }

        try:
            req = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                buf = ""
                for raw_line in resp:
                    try:
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
                    except Exception:
                        continue
                    buf += line + "\n"

                    if line.strip() == "":
                        event_data_lines = []
                        for b_line in buf.split("\n"):
                            b_line = b_line.strip()
                            if b_line.startswith("data:"):
                                data_str = b_line[5:].lstrip()
                                if data_str == "[DONE]":
                                    return
                                event_data_lines.append(data_str)
                        buf = ""

                        for data_str in event_data_lines:
                            if not data_str:
                                continue
                            try:
                                chunk = json.loads(data_str)
                                choices = chunk.get("choices", [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "") or choices[0].get("message", {}).get("content", "")
                                if content:
                                    yield content
                            except Exception:
                                continue
        except urllib.error.HTTPError as he:
            err_body = ""
            try:
                err_body = he.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            yield f"【API 错误】HTTP {he.code}: {he.reason}\n请求地址: `{self.endpoint}`\n模型: `{self.model}`\n{err_body}"
        except Exception as e:
            yield f"【网络异常】无法连接至大模型服务 `{self.endpoint}` ({self.model}): {e}"
