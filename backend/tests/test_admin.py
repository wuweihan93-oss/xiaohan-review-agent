"""验证管理员鉴权、会话撤销、只读查询和匿名访客隔离，使用临时测试数据库。"""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from psycopg import AsyncConnection

from app.services import review as review_service
from app import main
from app.security import admin
from app.security.passwords import hash_password, verify_password
from app.core.config import DB_URL

PASSWORD = 'test-only-password-9281'
PASSWORD_HASH = hash_password(PASSWORD)


class PasswordTests(unittest.TestCase):
    def test_local_and_container_setup_accept_eight_characters_but_reject_seven(self):
        from app import admin_setup
        spec = importlib.util.spec_from_file_location('local_admin_setup', Path(__file__).resolve().parents[2] / 'scripts/setup_admin.py')
        local_setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(local_setup)
        for setup in (admin_setup, local_setup):
            with self.subTest(entry=setup.__name__), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / '.env'
                setup.save_credentials(target, 'admin', 'Abcd1234')
                saved = target.read_text()
                encoded = saved.split("ADMIN_PASSWORD_HASH='", 1)[1].split("'", 1)[0]
                self.assertTrue(verify_password('Abcd1234', encoded))
                with self.assertRaisesRegex(ValueError, '8 至 256'):
                    setup.save_credentials(target, 'admin', 'Abcd123')
                self.assertEqual(saved, target.read_text())

    def test_random_salt_verification_and_invalid_hash(self):
        other = hash_password(PASSWORD)
        self.assertNotEqual(PASSWORD_HASH, other)
        self.assertTrue(verify_password(PASSWORD, PASSWORD_HASH))
        self.assertFalse(verify_password('wrong', PASSWORD_HASH))
        for value in ('', 'scrypt$wrong$hash', 'unknown$' + '0' * 32 + '$' + '0' * 128):
            self.assertFalse(verify_password(PASSWORD, value))

    def test_setup_preserves_other_env_and_quotes_dollar_signs(self):
        spec = importlib.util.spec_from_file_location('admin_setup', Path(__file__).resolve().parents[2] / 'scripts/setup_admin.py')
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / '.env'
            target.write_text("OTHER_SETTING=keep\nADMIN_USERNAME=old\nADMIN_PASSWORD_HASH=old\n")
            setup.save_credentials(target, 'study_admin', PASSWORD)
            content = target.read_text()
            self.assertIn('OTHER_SETTING=keep', content)
            self.assertIn("ADMIN_USERNAME='study_admin'", content)
            self.assertIn("ADMIN_PASSWORD_HASH='scrypt$", content)
            self.assertNotIn(PASSWORD, content)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            for username, password in (('bad name', PASSWORD), ('admin', 'short')):
                with self.assertRaises(ValueError):
                    setup.save_credentials(target, username, password)
            self.assertEqual(content, target.read_text())


class AdminAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        for key, value in {'DB_URL': 'isolated-test', 'ADMIN_USERNAME': 'admin', 'ADMIN_PASSWORD_HASH': PASSWORD_HASH}.items():
            patcher = patch.object(admin, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        admin._attempts.clear()
        admin._global_attempts.clear()

    async def login(self, **changes):
        return await self.client.post('/api/admin/login', json={'username': 'admin', 'password': PASSWORD, **changes}, headers={'X-Admin-Request': '1', 'Origin': 'http://test'})

    async def test_every_read_endpoint_rejects_public_and_visitor_cookies(self):
        visitor = await self.client.get('/api/visitor')
        with patch.object(admin.admin_store, 'overview', new=AsyncMock()) as store:
            for path in ('/session', '/overview', '/turns', '/conversations', '/conversations/some-id'):
                response = await self.client.get('/api/admin' + path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn(visitor.json()['cache_scope'], response.text)
            store.assert_not_awaited()

    async def test_disabled_admin_does_not_have_default_password(self):
        with patch.object(admin, 'ADMIN_PASSWORD_HASH', ''):
            self.assertEqual((await self.login()).status_code, 503)
        with patch.object(admin, 'DB_URL', ''):
            self.assertEqual((await self.login()).status_code, 503)

    async def test_cookie_is_separate_http_only_and_database_stores_only_hash(self):
        with patch.object(admin.admin_store, 'create_session', new=AsyncMock()) as create:
            response = await self.login()
        self.assertEqual(response.status_code, 200)
        token = self.client.cookies.get(admin.COOKIE_NAME)
        self.assertNotIn(token, response.text)
        self.assertNotEqual(create.call_args.args[0], token)
        self.assertEqual(create.call_args.args[0], admin.token_hash(token))
        self.assertEqual(create.call_args.args[1], response.json()['csrf_token'])
        header = response.headers['set-cookie']
        for flag in ('HttpOnly', 'SameSite=strict', 'Path=/api/admin', 'Max-Age=28800'):
            self.assertIn(flag, header)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_cross_site_login_and_missing_custom_header_are_rejected(self):
        body = {'username': 'admin', 'password': PASSWORD}
        with patch.object(admin.admin_store, 'create_session', new=AsyncMock()) as create:
            missing = await self.client.post('/api/admin/login', json=body)
            cross = await self.client.post('/api/admin/login', json=body, headers={'X-Admin-Request': '1', 'Origin': 'https://evil.example'})
            self.assertEqual(missing.status_code, 403)
            self.assertEqual(cross.status_code, 403)
            create.assert_not_awaited()

    async def test_https_cookie_and_same_origin_with_custom_port(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='https://test:8888') as secure:
            with patch.object(admin.admin_store, 'create_session', new=AsyncMock()):
                response = await secure.post('/api/admin/login', json={'username': 'admin', 'password': PASSWORD}, headers={'X-Admin-Request': '1', 'Origin': 'https://test:8888'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Secure', response.headers['set-cookie'])

    async def test_bad_password_and_username_use_same_error_and_rate_limit(self):
        first = await self.login(password='wrong')
        second = await self.login(username='unknown')
        self.assertEqual(first.status_code, 401)
        self.assertEqual(first.json(), second.json())
        for _ in range(3):
            await self.login(password='wrong')
        limited = await self.login()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers['retry-after'], '60')

    async def test_logout_requires_csrf_and_removes_server_session(self):
        self.client.cookies.set(admin.COOKIE_NAME, 'a' * 64, path='/api/admin')
        session = {'csrf_token': 'csrf', 'expires_at': datetime.now(timezone.utc) + timedelta(hours=1)}
        with patch.object(admin.admin_store, 'read_session', new=AsyncMock(return_value=session)), patch.object(admin.admin_store, 'remove_session', new=AsyncMock()) as remove:
            denied = await self.client.post('/api/admin/logout')
            self.assertEqual(denied.status_code, 403)
            remove.assert_not_awaited()
            allowed = await self.client.post('/api/admin/logout', headers={'X-CSRF-Token': 'csrf', 'Origin': 'http://test'})
            self.assertEqual(allowed.status_code, 200)
            remove.assert_awaited_once_with(admin.token_hash('a' * 64))
        with patch.object(admin.admin_store, 'read_session', new=AsyncMock(return_value=None)):
            self.assertEqual((await self.client.get('/api/admin/overview')).status_code, 401)


@unittest.skipUnless(DB_URL and os.getenv('XIAOHAN_POSTGRES_TESTS') == '1', '需要显式启用 PostgreSQL 集成测试')
class AdminDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        admin._attempts.clear()
        admin._global_attempts.clear()
        self.ids = [str(uuid.uuid4()) for _ in range(3)]
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        self.clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') for _ in range(3)]
        self.patches = [patch.object(admin, 'ADMIN_USERNAME', 'admin'), patch.object(admin, 'ADMIN_PASSWORD_HASH', PASSWORD_HASH), patch.object(review_service, 'list_materials', return_value=[{'id': 'study'}]), patch.object(review_service, 'review', new=AsyncMock(return_value={'answer': '隔离测试回答', 'sources': [], 'trace': []}))]
        for item in self.patches:
            item.start()
        for index in range(2):
            response = await self.clients[index].post('/api/review', json={'conversation_id': self.ids[index], 'question': f'后台测试问题{index}-{self.ids[index]}', 'material_ids': ['study']})
            self.assertEqual(response.status_code, 200)
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            await connection.execute('INSERT INTO xiaohan_conversations (id, title) VALUES (%s, %s)', (self.ids[2], f'旧记录-{self.ids[2]}'))
        self.admin = self.clients[2]
        response = await self.admin.post('/api/admin/login', json={'username': 'admin', 'password': PASSWORD}, headers={'X-Admin-Request': '1'})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json()['csrf_token']
        self.token_hash = admin.token_hash(self.admin.cookies.get(admin.COOKIE_NAME))

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            await connection.execute('DELETE FROM xiaohan_conversations WHERE id = ANY(%s)', (self.ids,))
            await connection.execute('DELETE FROM xiaohan_admin_sessions WHERE token_hash = %s', (self.token_hash,))
        for item in reversed(self.patches):
            item.stop()
        await self.lifespan.__aexit__(None, None, None)

    async def test_admin_sees_both_visitors_legacy_search_filter_and_pagination(self):
        listing = (await self.admin.get('/api/admin/conversations?limit=100')).json()
        self.assertTrue(set(self.ids).issubset({row['id'] for row in listing['conversations']}))
        first = (await self.admin.get(f'/api/admin/conversations/{self.ids[0]}')).json()['conversation']
        self.assertEqual(len(first['messages']), 2)
        owner = first['visitor_id']
        filtered = (await self.admin.get('/api/admin/conversations', params={'visitor_id': owner})).json()
        self.assertEqual([row['id'] for row in filtered['conversations']], [self.ids[0]])
        found = (await self.admin.get('/api/admin/conversations', params={'q': self.ids[1]})).json()
        self.assertEqual(found['total'], 1)
        literal = (await self.admin.get('/api/admin/conversations', params={'q': "%' OR 1=1 --"})).json()
        self.assertEqual(literal['total'], 0)
        legacy = (await self.admin.get('/api/admin/conversations?legacy=true')).json()
        self.assertIn(self.ids[2], [row['id'] for row in legacy['conversations']])
        page_one = (await self.admin.get('/api/admin/conversations?limit=1')).json()
        page_two = (await self.admin.get('/api/admin/conversations?limit=1&offset=1')).json()
        self.assertNotEqual(page_one['conversations'][0]['id'], page_two['conversations'][0]['id'])
        overview = (await self.admin.get('/api/admin/overview')).json()
        self.assertGreaterEqual(overview['conversations'], 3)
        self.assertGreaterEqual(overview['visitors'], 2)
        # 管理员登录不使公开访客 API 获得额外权限。
        self.assertEqual((await self.clients[1].get(f'/api/conversations/{self.ids[0]}')).status_code, 404)
        self.assertEqual((await self.admin.get(f'/api/conversations/{self.ids[0]}')).status_code, 404)

    async def test_new_saved_messages_visible_and_server_restart_preserves_admin_session(self):
        await self.clients[0].post('/api/review', json={'conversation_id': self.ids[0], 'question': '新增一轮复盘', 'material_ids': ['study']})
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        saved = (await self.admin.get(f'/api/admin/conversations/{self.ids[0]}')).json()['conversation']
        self.assertEqual(len(saved['messages']), 4)
        self.assertEqual(saved['messages'][-2]['content'], '新增一轮复盘')

    async def test_table_pairs_questions_answers_and_records_ip_without_guessing_legacy_ip(self):
        from app.repositories.conversations import begin_turn, finish_turn
        detail = (await self.admin.get(f'/api/admin/conversations/{self.ids[0]}')).json()['conversation']
        owner = detail['visitor_id']
        table = (await self.admin.get('/api/admin/turns', params={'visitor_id': owner})).json()
        self.assertEqual(table['total'], 1)
        row = table['rows'][0]
        self.assertEqual(row['question'], detail['messages'][0]['content'])
        self.assertEqual(row['answer'], '隔离测试回答')
        self.assertEqual(row['client_ip'], '127.0.0.1')
        # 上一问没有助手消息时，不把下一问的回答错误配给上一问。
        await begin_turn(self.ids[0], '故意待保存的问题', [], 'coach', None, visitor_id=owner, client_ip='203.0.113.55')
        await begin_turn(self.ids[0], '新的用户问题', [], 'coach', None, visitor_id=owner, client_ip='203.0.113.55')
        await finish_turn(self.ids[0], '只回答新的问题', [], [], visitor_id=owner)
        pending = (await self.admin.get('/api/admin/turns', params={'q': '故意待保存'})).json()['rows']
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]['answer'])
        ip_rows = (await self.admin.get('/api/admin/turns', params={'q': '203.0.113.55'})).json()['rows']
        self.assertEqual(len(ip_rows), 2)
        self.assertEqual(ip_rows[0]['answer'], '只回答新的问题')
        self.assertEqual(ip_rows[0]['question'], '新的用户问题')
        self.assertIsNone(ip_rows[1]['answer'])
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            await connection.execute("INSERT INTO xiaohan_messages (id, conversation_id, role, content) VALUES (%s, %s, 'user', '旧版问题')", (str(uuid.uuid4()), self.ids[2]))
        legacy = (await self.admin.get('/api/admin/turns?legacy=true')).json()['rows']
        saved = next(item for item in legacy if item['conversation_id'] == self.ids[2])
        self.assertIsNone(saved['client_ip'])
        self.assertIsNone(saved['visitor_id'])

    async def test_expired_revoked_or_changed_password_sessions_cannot_read_messages(self):
        # 修改密码配置后旧会话失效，恢复配置可继续测试服务端撤销。
        with patch.object(admin, 'ADMIN_PASSWORD_HASH', hash_password('new-test-password-123')):
            self.assertEqual((await self.admin.get('/api/admin/overview')).status_code, 401)
        self.assertEqual((await self.admin.get('/api/admin/overview')).status_code, 200)
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            await connection.execute("UPDATE xiaohan_admin_sessions SET expires_at = now() - interval '1 second' WHERE token_hash = %s", (self.token_hash,))
        self.assertEqual((await self.admin.get('/api/admin/overview')).status_code, 401)
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            await connection.execute("UPDATE xiaohan_admin_sessions SET expires_at = now() + interval '1 hour' WHERE token_hash = %s", (self.token_hash,))
        response = await self.admin.post('/api/admin/logout', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200)
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            cursor = await connection.execute('SELECT count(*) FROM xiaohan_admin_sessions WHERE token_hash = %s', (self.token_hash,))
            self.assertEqual((await cursor.fetchone())[0], 0)
