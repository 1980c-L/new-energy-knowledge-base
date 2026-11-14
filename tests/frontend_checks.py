"""浏览器回归：真实前端+模拟Dify响应，禁止请求模型供应商。"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request
import urllib.error
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
ANSWER = (ROOT / 'tests/fixtures/answer.md').read_text(encoding='utf-8')
NOTE = '未提供项目结构化字段；本轮给候选证据，供人工核对。'


def sse(events):
    return ''.join('data: ' + json.dumps(ev, ensure_ascii=False) + '\n\n' for ev in events)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8109')
    parser.add_argument('--label', default='browser-initial')
    args = parser.parse_args()
    output = ROOT / 'results' / args.label
    output.mkdir(parents=True, exist_ok=False)
    checks = []

    def record(name, passed, detail):
        checks.append(dict(name=name, passed=bool(passed), detail=detail))
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)

        async def fixture(chat_body, delayed=False, history=False):
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            page = await context.new_page()
            gate, started = asyncio.Event(), asyncio.Event()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            async def api(route):
                path = route.request.url.split('/api/', 1)[1]
                data = {}
                if path.startswith('passport'):
                    data = {'access_token': 'test-visitor-only'}
                elif path == 'site':
                    data = {'site': {'title': '新能源知识库'}}
                elif path == 'parameters':
                    data = {'suggested_questions': ['演示问题']}
                elif path.startswith('conversations?'):
                    data = {'data': ([{'id': 'A', 'name': '会话A'}, {'id': 'B', 'name': '会话B'}]
                        if history and 'pinned=false' in path else [])}
                elif path.startswith('messages?'):
                    is_a = 'conversation_id=A' in path
                    if is_a:
                        started.set()
                        await gate.wait()
                    who = 'A' if is_a else 'B'
                    data = {'data': [{'id': 'm' + who, 'query': who + '的问题', 'answer': who + '的回答'}]}
                elif path == 'chat-messages':
                    started.set()
                    if delayed:
                        await gate.wait()
                    try:
                        await route.fulfill(status=200, content_type='text/event-stream', body=chat_body)
                    except Exception:
                        pass  # Intentional client abort in the navigation test.
                    return
                await route.fulfill(status=200, content_type='application/json', body=json.dumps(data))

            await page.route('**/api/**', api)
            await page.goto(args.base, wait_until='networkidle')
            await page.locator('.suggestion').first.wait_for()
            return context, page, gate, started, errors

        body = sse([{'event': 'message', 'answer': ANSWER, 'conversation_id': 'C', 'message_id': 'M'},
                    {'event': 'message_end', 'conversation_id': 'C', 'message_id': 'M'}])
        context, page, _, _, errors = await fixture(body)
        parsed = await page.evaluate('(s) => window.MiniMarkdown.parseAnswer(s)', ANSWER)
        record('parser_preserves_limitation_note', NOTE in (parsed or {}).get('notes', []), parsed and {'notes': parsed.get('notes')})
        await page.locator('#collapseBtn').click()
        record('desktop_sidebar_can_reopen', await page.locator('#openSidebarBtn').is_visible(), {})
        # Restore through the DOM solely to keep this regression independent.
        await page.evaluate('document.getElementById("app").classList.remove("collapsed")')
        await page.locator('.suggestion').first.click()
        await page.wait_for_function('!document.getElementById("sendBtn").disabled')
        # Wait two render frames to expose a pending RAF overwriting final cards.
        await page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
        text = await page.locator('.answer-body').inner_text()
        record('preserve_backend_note', NOTE in text, {'note_visible': NOTE in text})
        cards = await page.locator('.ev-card').count()
        record('final_cards_survive_animation_frame', cards == 4, {'cards': cards})
        record('supporting_reference_index_visible', await page.locator('.evidence-index').count() == 1,
               {'count': await page.locator('.evidence-index').count()})
        record('candidate_material_not_labeled_final_conclusion', '结论' not in (await page.locator('.sec-label').all_text_contents()),
               {'labels': await page.locator('.sec-label').all_text_contents()})
        record('browser_no_uncaught_error', not errors, errors)
        await page.screenshot(path=str(output / 'fixture-answer.png'), full_page=True)
        await context.close()

        for name, events in [
            ('truncated_stream_is_not_success', [{'event': 'message', 'answer': '只返回一部分材料'}]),
            ('sse_error_is_not_success', [{'event': 'error', 'message': '知识服务暂不可用'}]),
        ]:
            context, page, _, _, _ = await fixture(sse(events))
            await page.locator('.suggestion').first.click()
            await page.wait_for_function('!document.getElementById("sendBtn").disabled')
            status = await page.locator('.status-text').inner_text()
            record(name, status in ('回答中断', '执行失败', '服务暂不可用'), {'status': status})
            await context.close()

        # A normal click on "new chat" while a slow answer is pending.
        context, page, gate, started, _ = await fixture(body, delayed=True)
        await page.locator('.suggestion').first.click()
        await asyncio.wait_for(started.wait(), 10)
        await page.get_by_role('button', name='新建对话', exact=True).click()
        gate.set()
        await page.wait_for_function('!document.getElementById("sendBtn").disabled')
        record('new_chat_detaches_pending_answer', await page.locator('.msg').count() == 0,
               {'turns': await page.locator('.msg').count(), 'welcome': await page.locator('#welcome').is_visible()})
        # Next request must not inherit C from the response to the detached view.
        captured = []
        async def capture(route):
            captured.append(route.request.post_data_json)
            await route.fulfill(status=200, content_type='text/event-stream', body=body)
        await page.route('**/api/chat-messages', capture)
        await page.locator('#input').fill('新会话问题')
        await page.get_by_role('button', name='发送', exact=True).click()
        await page.wait_for_function('!document.getElementById("sendBtn").disabled')
        record('new_chat_does_not_reuse_old_conversation', captured and not captured[-1].get('conversation_id'), captured)
        await context.close()

        context, page, gate, started, _ = await fixture(body, history=True)
        await page.get_by_role('button', name='会话A', exact=True).click()
        await asyncio.wait_for(started.wait(), 10)
        await page.get_by_role('button', name='会话B', exact=True).click()
        await page.get_by_role('heading', name='B的问题', exact=True).wait_for()
        async with page.expect_response(lambda response: 'messages?conversation_id=A' in response.url) as response_info:
            gate.set()
        await response_info.value
        await page.wait_for_load_state('networkidle')
        await page.evaluate('() => new Promise(requestAnimationFrame)')
        titles = await page.locator('.q-title').all_text_contents()
        record('late_history_cannot_mix_conversations', titles == ['B的问题'], titles)
        await context.close()

        context, page, _, _, _ = await fixture(body, history=True)
        dialogs = []
        async def accept_dialog(dialog):
            dialogs.append(dialog.type)
            await dialog.accept('测试名称')
        page.on('dialog', accept_dialog)
        await page.locator('.conv-more').first.click()
        try:
            await page.get_by_role('button', name='重命名', exact=True).click(timeout=5000)
            record('conversation_menu_rename_clickable', dialogs == ['prompt'], dialogs)
        except Exception as error:
            await page.screenshot(path=str(output / 'menu-failure.png'), full_page=True)
            record('conversation_menu_rename_clickable', False, str(error))
        await context.close()

        # A conversation refresh finishing while its menu is open must not remove the menu.
        context, page, gate, started, _ = await fixture(body, history=True)
        async def delayed_list(route):
            if 'pinned=false' in route.request.url:
                started.set()
                await gate.wait()
                data = [{'id': 'A', 'name': '会话A'}, {'id': 'B', 'name': '会话B'}]
            else:
                data = []
            await route.fulfill(status=200, content_type='application/json', body=json.dumps({'data': data}))
        await page.route('**/api/conversations?**', delayed_list)
        await page.locator('.suggestion').first.click()
        await asyncio.wait_for(started.wait(), 10)
        await page.locator('.conv-more').first.click()
        gate.set()
        await page.wait_for_load_state('networkidle')
        record('background_refresh_preserves_open_menu', await page.locator('.conv-menu').count() == 1, {})
        await context.close()

        context, page, gate, started, _ = await fixture(body, history=True)
        submitted = []
        async def record_submission(route):
            submitted.append(route.request.post_data_json)
            await route.fulfill(status=200, content_type='text/event-stream', body=body)
        await page.route('**/api/chat-messages', record_submission)
        await page.get_by_role('button', name='会话A', exact=True).click()
        await asyncio.wait_for(started.wait(), 10)
        record('history_loading_disables_send', await page.locator('#sendBtn').is_disabled(), {})
        await page.locator('#input').fill('历史还在读取时的新问题')
        await page.locator('#input').press('Enter')
        await page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
        record('submit_cannot_overtake_history', len(submitted) == 0, {'requests': len(submitted)})
        gate.set()
        await page.wait_for_load_state('networkidle')
        await page.get_by_role('heading', name='A的问题', exact=True).wait_for()
        history_titles = await page.locator('.q-title').all_text_contents()
        record('history_load_retains_message_order', history_titles == ['A的问题'], history_titles)
        await context.close()
        await browser.close()

    # Exercise the actual local proxy class against a private mock upstream.
    spec = importlib.util.spec_from_file_location('web_serve', ROOT / 'web/serve.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Upstream(BaseHTTPRequestHandler):
        def do_DELETE(self):
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def log_message(self, *args):
            pass
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    module.UPSTREAM_PORT = upstream.server_port
    proxy = module.Server(('127.0.0.1', 0), module.Handler)
    for server in (upstream, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request('http://127.0.0.1:' + str(proxy.server_address[1]) + '/api/conversations/test-only', method='DELETE')
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                status = res.status
        except urllib.error.HTTPError as error:
            status = error.code
        record('proxy_forwards_delete', status == 204, {'status': status})
    finally:
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
    result = {'kind': 'browser_with_simulated_dify_and_private_proxy_upstream', 'checks': checks,
              'passed': sum(c['passed'] for c in checks), 'total': len(checks)}
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if result['passed'] == result['total'] else 1)


if __name__ == '__main__':
    asyncio.run(main())
