# -*- coding: utf-8 -*-
"""API Key 健康检查（缓存加固配套，DS+Claude 方案共识）：
最小 chat 请求探测（max_tokens=1，"ping"），与真实调用同 endpoint/同鉴权/同模型，
可真实暴露 401/403/404(model)/429/超时；GET /models 中转站多不支持，故不用。
- TTL 300s 结果缓存（防重复打 API）；force=True 强制重探
- 纯 urllib 实现（与 ai_tutor 一致，无新依赖）；不重试（健康检查不吞错）
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

_HEALTH_TTL = 300.0          # 5 分钟内同配置不重复探测
_health_cache: dict[str, "HealthResult"] = {}
_health_lock = threading.Lock()


@dataclass
class HealthResult:
    ok: bool
    status: str            # "ok" | "auth" | "model" | "rate" | "net" | "unknown"
    detail: str
    latency_ms: int
    checked_at: float


def _key_fp(api_key: str, base_url: str, model: str) -> str:
    """指纹：key 前 8+后 4 + base + model（不明文存 key）"""
    if not api_key:
        return f"|{base_url}|{model}"
    return f"{api_key[:8]}...{api_key[-4:]}|{base_url}|{model}"


def _classify(status_code: int) -> HealthResult:
    if status_code == 200:
        return None  # 由调用方构造（带延迟）
    if status_code in (401, 403):
        return HealthResult(False, "auth", f"鉴权失败 HTTP {status_code}（Key 无效/过期/无权限）", 0, time.time())
    if status_code == 404:
        return HealthResult(False, "model", f"模型或路径不存在 HTTP 404", 0, time.time())
    if status_code == 429:
        return HealthResult(False, "rate", "限流 HTTP 429（Key 有效但被限）", 0, time.time())
    return HealthResult(False, "unknown", f"HTTP {status_code}", 0, time.time())


def probe_key(api_key: str, base_url: str, model: str,
              timeout: float = 8.0, force: bool = False) -> HealthResult:
    """最小 chat 探测。空配置直接返回 auth 失败；TTL 内命中缓存不重复请求。"""
    if not api_key or not api_key.strip() or not base_url or not model:
        return HealthResult(False, "auth", "配置不完整（Key/Base URL/Model 缺一不可）", 0, time.time())

    fp = _key_fp(api_key, base_url, model)
    now = time.time()
    with _health_lock:
        cached = _health_cache.get(fp)
        if cached and not force and (now - cached.checked_at) < _HEALTH_TTL:
            return cached

    t0 = time.time()
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept-Encoding": "identity",
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()  # 200 即视为可用（无需解析内容）
        res = HealthResult(True, "ok", "正常", int((time.time() - t0) * 1000), time.time())
    except urllib.error.HTTPError as he:
        res = _classify(he.code)
        res.latency_ms = int((time.time() - t0) * 1000)
        res.checked_at = time.time()
    except urllib.error.URLError as ue:
        res = HealthResult(False, "net", f"网络错误: {ue.reason}", int((time.time() - t0) * 1000), time.time())
    except Exception as e:
        res = HealthResult(False, "net", f"{type(e).__name__}", int((time.time() - t0) * 1000), time.time())

    with _health_lock:
        _health_cache[fp] = res
    return res


def clear_health_cache() -> None:
    """清空探测结果缓存（测试/手动重置用）"""
    with _health_lock:
        _health_cache.clear()
