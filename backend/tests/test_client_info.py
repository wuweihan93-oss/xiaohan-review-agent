"""来源 IP 只能从真实连接或明确可信的反向代理获得。"""

import socket
import unittest
from unittest.mock import patch

import httpx
from starlette.requests import Request
from app.security import client_info


def request(peer='198.51.100.10', **headers):
    return Request({'type': 'http', 'client': (peer, 1234), 'headers': [(key.lower().replace('_', '-').encode(), value.encode()) for key, value in headers.items()]})


class ClientIpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        client_info._proxy_cache.clear()
        for name in ('TRUSTED_PROXY_HOSTS', 'TRUSTED_PROXY_CIDRS'):
            item = patch.object(client_info, name, [])
            item.start()
            self.addCleanup(item.stop)

    async def test_direct_client_cannot_spoof_forwarded_address(self):
        source = await client_info.client_ip(request(x_real_ip='1.2.3.4', x_forwarded_for='5.6.7.8'))
        self.assertEqual(source, '198.51.100.10')

    async def test_only_configured_proxy_network_can_supply_real_ip(self):
        with patch.object(client_info, 'TRUSTED_PROXY_CIDRS', ['invalid', '172.22.0.0/16']):
            self.assertEqual(await client_info.client_ip(request('172.22.0.5', x_real_ip='203.0.113.8', x_forwarded_for='1.1.1.1')), '203.0.113.8')
            self.assertEqual(await client_info.client_ip(request('172.22.0.5', x_real_ip='not-an-ip')), '172.22.0.5')
            self.assertEqual(await client_info.client_ip(request('198.51.100.10', x_real_ip='203.0.113.8')), '198.51.100.10')

    async def test_docker_frontend_dns_is_explicitly_trusted_and_cached(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('172.22.0.5', 0))]
        with patch.object(client_info, 'TRUSTED_PROXY_HOSTS', ['frontend']), patch.object(client_info.socket, 'getaddrinfo', return_value=records) as lookup:
            for _ in range(2):
                self.assertEqual(await client_info.client_ip(request('172.22.0.5', x_real_ip='2001:db8::1')), '2001:db8::1')
            self.assertEqual(await client_info.client_ip(request('172.22.0.9', x_real_ip='1.2.3.4')), '172.22.0.9')
            lookup.assert_called_once()

    async def test_dns_failure_or_missing_connection_does_not_trust_headers(self):
        with patch.object(client_info, 'TRUSTED_PROXY_HOSTS', ['frontend']), patch.object(client_info.socket, 'getaddrinfo', side_effect=OSError('模拟无法解析代理')):
            self.assertEqual(await client_info.client_ip(request(x_real_ip='1.2.3.4')), '198.51.100.10')
        self.assertIsNone(await client_info.client_ip(Request({'type': 'http', 'headers': [], 'client': None})))

    async def test_only_trusted_proxy_can_set_https_and_secure_visitor_cookie(self):
        from app import main
        with patch.object(client_info, 'TRUSTED_PROXY_CIDRS', ['172.22.0.0/16']):
            for peer, secure in [('172.22.0.5', True), ('198.51.100.10', False)]:
                transport = httpx.ASGITransport(app=main.app, client=(peer, 1234))
                async with httpx.AsyncClient(transport=transport, base_url='http://test:8888') as client:
                    response = await client.get('/api/visitor', headers={'X-Forwarded-Proto': 'https', 'X-Real-IP': '203.0.113.1'})
                self.assertEqual('Secure' in response.headers['set-cookie'], secure)
