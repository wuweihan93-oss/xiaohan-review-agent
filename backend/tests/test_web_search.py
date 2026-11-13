"""联网搜索使用模拟 HTTP，不消耗真实搜索额度，也不发送学习资料。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.integrations import web_search


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, payload=None, *, status=200, error=None):
        response = httpx.Response(status, content=json.dumps(payload).encode())
        with patch.object(web_search, "TAVILY_API_KEY", "测试占位密钥"), patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=response, side_effect=error)) as post:
            result = await web_search.search_web("LangChain 官方文档")
        return result, post

    async def test_results_keep_citable_urls_and_bound_content_without_provider_scores(self):
        results, post = await self.search({"results": [
            {"title": "官方文档", "url": "https://docs.langchain.com/oss/python/langchain/agents", "content": "正文" * 1500, "score": 0.99},
            {"title": "重复结果", "url": "https://docs.langchain.com/oss/python/langchain/agents", "content": "重复内容"},
            {"title": "不安全链接", "url": "javascript:alert(1)", "content": "内容"},
            {"title": "无摘要", "url": "https://example.com/no-text"},
        ]})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "web")
        self.assertEqual(results[0]["source"], results[0]["url"])
        self.assertLessEqual(len(results[0]["text"]), 1800)
        self.assertNotIn("score", results[0])
        self.assertNotIn("测试占位密钥", str(results))
        self.assertEqual(post.call_args.kwargs["json"]["query"], "LangChain 官方文档")
        self.assertFalse(post.call_args.kwargs["json"]["include_raw_content"])

    async def test_results_are_limited_and_empty_results_are_valid(self):
        results, _ = await self.search({"results": [{"url": f"https://example.com/{index}", "content": "摘要"} for index in range(10)]})
        self.assertEqual(len(results), 5)
        results, _ = await self.search({"results": []})
        self.assertEqual(results, [])

    async def test_missing_key_stops_before_network(self):
        with patch.object(web_search, "TAVILY_API_KEY", ""), patch.object(httpx.AsyncClient, "post", new=AsyncMock()) as post:
            with self.assertRaisesRegex(RuntimeError, "TAVILY_API_KEY"):
                await web_search.search_web("测试")
        post.assert_not_awaited()

    async def test_timeout_auth_and_quota_failures_are_chinese_and_do_not_echo_provider_secrets(self):
        for status, expected in [(401, "认证失败"), (403, "认证失败"), (429, "额度"), (432, "额度"), (500, "暂时不可用")]:
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeError, expected) as caught:
                    await self.search({"detail": "不得显示的服务端正文"}, status=status)
                self.assertNotIn("不得显示", str(caught.exception))
        with self.assertRaisesRegex(RuntimeError, "超时"):
            await self.search(error=httpx.ReadTimeout("测试超时"))
        with self.assertRaisesRegex(RuntimeError, "无法连接"):
            await self.search(error=httpx.ConnectError("连接失败"))

    async def test_malformed_results_are_not_treated_as_search_success(self):
        for payload in (None, [], {"error": "错误"}, {"results": "不是列表"}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(RuntimeError, "格式不正确"):
                    await self.search(payload)


class WebUrlTests(unittest.TestCase):
    def test_only_http_links_without_embedded_credentials_are_accepted(self):
        self.assertEqual(web_search.safe_web_url("https://example.com/docs"), "https://example.com/docs")
        for value in (None, "javascript:alert(1)", "file:///etc/passwd", "https://user:password@example.com", "https://[", "https://example.com:bad"):
            with self.subTest(value=value):
                self.assertEqual(web_search.safe_web_url(value), "")
