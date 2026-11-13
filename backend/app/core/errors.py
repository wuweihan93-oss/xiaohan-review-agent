"""将服务异常转换为页面可读的中文提示。"""


def friendly_error(action: str, exc: Exception) -> str:
    raw = str(exc).strip()
    lowered = raw.lower()
    if "tool_calls" in lowered and "tool" in lowered:
        return f"{action}失败：对话中的工具记录不完整，请重试当前问题；系统会自动修复中断的工具记录。"
    if "api key" in lowered or "authentication" in lowered or "unauthorized" in lowered:
        reason = "模型认证失败，请检查项目 .env 中的 DEEPSEEK_API_KEY。"
    elif "milvus" in lowered or "collection" in lowered:
        reason = "向量数据库操作失败，请确认 Milvus Lite 索引状态。"
    elif "timeout" in lowered or "timed out" in lowered:
        reason = "模型请求超时，请检查网络后重试。"
    elif "429" in lowered or "rate limit" in lowered:
        reason = "模型服务请求过于频繁，请稍后重试。"
    elif isinstance(exc, ValueError):
        return raw
    else:
        reason = "请检查文件格式和模型配置后重试。"
    return f"{action}失败：{reason} 技术详情：{raw[:300]}".strip()
