"""进程级共享服务实例。

PDFService 构造约 2.7ms，实例目前无可变状态（`__init__` 只保存 `_assets_dir`
和一个未被使用的 Markdown 对象），因此进程内共享是安全的。

这里用 `functools.lru_cache` 而不是 `st.cache_resource`：
- 不参与 Streamlit 的缓存 key 计算，不随主脚本 rerun 重新定义；
- 没有「缓存资源永不淘汰、跨会话共享」的隐式语义，就是一个普通的 Python 单例。

注意：如果以后给 PDFService 增加按调用写入的实例状态（复用 `_md`、缓存渲染结果、
持有临时目录等），这个单例需要重新评估，否则会跨会话串数据。
"""
from functools import lru_cache

from core.pdf_service import PDFService


@lru_cache(maxsize=1)
def get_pdf_service() -> PDFService:
    """返回进程内唯一的 PDFService 实例。"""
    return PDFService()
