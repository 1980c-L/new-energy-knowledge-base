"""本地预览服务：静态文件 + /api 反向代理到 Dify。

只用于本机预览与验收；正式部署由 Codex 用 nginx 做同样的同源反代，
前端代码不需要任何改动（apiBase 保持空字符串）。

    python serve.py [--port 8099] [--upstream 127.0.0.1:8088]
"""

import argparse
import http.client
import http.server
import os
import socketserver
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8088
# 请求方向：逐跳头 + host + content-length（由 http.client 依据 body 重新计算）
REQ_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "upgrade", "host", "content-length"}
# 响应方向：只去掉逐跳头，content-length 必须保留，
# 否则下游拿不到正文长度，HTTP/1.1 keep-alive 下会一直等响应结束。
RESP_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-connection"}


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("[api] %s\n" % (fmt % args))

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in REQ_HOP_HEADERS}

        conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=180)
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:
            payload = ('{"error":"upstream_unavailable","detail":"%s"}' % type(exc).__name__).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(resp.status)
        has_length = False
        for key, value in resp.getheaders():
            if key.lower() in RESP_HOP_HEADERS:
                continue
            if key.lower() == "content-length":
                has_length = True
            self.send_header(key, value)
        if not has_length:
            # 上游是 chunked（如 SSE 流式回答）：下游收不到长度，
            # 只能靠关闭连接标记正文结束。
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        # read1 有数据就返回，SSE 才会逐块下发；read(n) 会凑满 n 字节才返回。
        read_chunk = getattr(resp, "read1", resp.read)
        while True:
            chunk = read_chunk(4096)
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
        conn.close()

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy("POST")
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy("DELETE")
        else:
            self.send_error(405)

    def do_HEAD(self):
        if self.path.startswith("/api/"):
            self._proxy("HEAD")
        else:
            super().do_HEAD()


class Server(socketserver.ThreadingTCPServer):
    # Windows 上 SO_REUSEADDR 允许第二个进程绑同一端口，旧进程没退就会
    # 悄悄继续占着服务，改动看不出效果。只在 POSIX 上开启。
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--upstream", default="127.0.0.1:8088")
    args = parser.parse_args()

    global UPSTREAM_HOST, UPSTREAM_PORT
    host, _, port = args.upstream.partition(":")
    UPSTREAM_HOST = host or "127.0.0.1"
    UPSTREAM_PORT = int(port or 8088)

    with Server(("127.0.0.1", args.port), Handler) as httpd:
        print("serving %s on http://127.0.0.1:%d  (api -> %s)" % (ROOT, args.port, args.upstream))
        httpd.serve_forever()


if __name__ == "__main__":
    main()
