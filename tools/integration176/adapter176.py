"""Serialized, authenticated local HTTP tool; no external API/provider client."""
import argparse
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path

from bridge176 import Bridge, render


def create_server(host, port, bridge, key, display_store=None):
    if not isinstance(key, str) or len(key) < 32:
        raise ValueError('adapter_key_too_short')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, plain=False):
            payload = (body if plain else json.dumps(body, ensure_ascii=False)).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', ('text/plain' if plain else 'application/json') + '; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == '/health':
                try:
                    bridge.bundles.load(bridge.candidate)
                    self.send(200, dict(state='ready', schema_version='energy-local/v1',
                                        candidate_id=bridge.candidate, generation='disabled'))
                except (ValueError, OSError):
                    self.send(503, dict(state='source_error'))
            else:
                self.send(404, dict(state='not_found'))

        def do_POST(self):
            if self.path not in {'/query', '/dify-query'}:
                return self.send(404, dict(state='not_found'))
            if not compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + key):
                return self.send(403, dict(state='unauthorized'))
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    return self.send(413, dict(state='input_error', error='body_size'))
                def invalid_constant(value):
                    raise ValueError('nonfinite_json')
                request = json.loads(self.rfile.read(size), parse_constant=invalid_constant)
                if self.path == '/dify-query' and isinstance(request, dict):
                    project = request.pop('project_json', '')
                    if project and isinstance(project, str):
                        try:
                            request['project'] = json.loads(project, parse_constant=invalid_constant)
                        except ValueError:
                            result = bridge.query(dict(question=''))
                            result['error'] = dict(code='invalid_project_json', detail='项目字段JSON格式不正确')
                            return self.send(200, render(result), plain=True)
                result = bridge.query(request)
                if display_store:
                    prefix = str(bridge.bundles.root).replace('\\', '/')
                    for ref in result['references']:
                        for field in ('local_original', 'local_normalized'):
                            ref[field] = ref[field].replace('\\', '/').replace(prefix, display_store.rstrip('/'), 1)
                result['rendered'] = render(result)
                # A valid tool envelope carries validation state as data. Dify otherwise
                # discards non-2xx response bodies and confuses missing fields with outages.
                code = 503 if result['state'] == 'source_error' else 200
                self.send(code, result['rendered'] if self.path == '/dify-query' else result,
                          plain=self.path == '/dify-query')
            except (ValueError, UnicodeError, TypeError):
                self.send(400, dict(state='input_error', error='invalid_json'))

    # Bridge/171 are explicitly single-session and serial, so do not use ThreadingHTTPServer.
    server = HTTPServer((host, port), Handler)
    server.timeout = 2
    return server


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--store', type=Path, required=True)
    p.add_argument('--candidate', required=True)
    p.add_argument('--key-file', type=Path, required=True)
    p.add_argument('--bind', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8080)
    p.add_argument('--display-store')
    a = p.parse_args()
    bridge = Bridge(a.store, a.candidate)
    server = create_server(a.bind, a.port, bridge, a.key_file.read_text(encoding='utf-8').strip(), a.display_store)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        bridge.close()


if __name__ == '__main__':
    main()
