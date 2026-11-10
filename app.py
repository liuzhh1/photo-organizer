from __future__ import annotations

import bootstrap  # noqa: F401; set project-local dependencies before other imports

import argparse
import hmac
import json
import mimetypes
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from library import Library
from folders import FolderBrowser

ROOT = Path(__file__).resolve().parent


def make_server(library, port=0):
    token = secrets.token_urlsafe(32)
    folders = FolderBrowser(library.data)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Keep the launcher quiet; background task failures are shown in the UI.
            if args and str(args[1] if len(args) > 1 else '').startswith('5'):
                super().log_message(fmt, *args)

        def send(self, payload, status=200, content_type='application/json; charset=utf-8'):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            elif isinstance(payload, str):
                payload = payload.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def do_GET(self):
            if not self.valid_host():
                return self.send({'error': '无效的主机'}, 403)
            parsed = urlparse(self.path)
            route = parsed.path
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                if route == '/api/health':
                    return self.send({'app': 'frame-photo', 'pid': os.getpid(),
                                      'data': str(library.data), 'status': 'ok'})
                if route == '/api/state':
                    return self.send(library.state())
                if route == '/api/assets':
                    return self.send(library.list_assets(params))
                if route == '/api/recommend':
                    return self.send(library.recommend())
                if route.startswith('/thumbs/'):
                    name = route.rsplit('/', 1)[-1]
                    if len(name) != 28 or not name.endswith('.jpg') or any(c not in '0123456789abcdef' for c in name[:-4]):
                        raise FileNotFoundError()
                    return self.send((library.data / 'thumbs' / name).read_bytes(), content_type='image/jpeg')
                if route.startswith('/preview/'):
                    asset_id = route.rsplit('/', 1)[-1]
                    from contextlib import closing
                    from imaging import open_image
                    import io
                    with closing(library.connect()) as db:
                        row = db.execute('SELECT path FROM assets WHERE id=?', (asset_id,)).fetchone()
                    if not row:
                        raise FileNotFoundError()
                    image = open_image(Path(row['path']))
                    image.thumbnail((2400, 2400))
                    output = io.BytesIO()
                    image.save(output, 'JPEG', quality=92)
                    return self.send(output.getvalue(), content_type='image/jpeg')
                if route == '/':
                    html = (ROOT / 'web' / 'index.html').read_text('utf-8').replace('__SESSION_TOKEN__', token)
                    return self.send(html, content_type='text/html; charset=utf-8')
                if route in {'/app.js', '/style.css'}:
                    file = ROOT / 'web' / route[1:]
                    content_type = 'text/javascript; charset=utf-8' if file.suffix == '.js' else 'text/css; charset=utf-8'
                    return self.send(file.read_bytes(), content_type=content_type)
                if route == '/favicon.ico':
                    return self.send(b'', status=204, content_type='image/x-icon')
                raise FileNotFoundError()
            except FileNotFoundError:
                self.send({'error': '文件不存在，原始素材可能已移动'}, 404)
            except (ValueError, TypeError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception as exc:
                self.send({'error': str(exc)}, 500)

        def do_POST(self):
            if not self.valid_host() or not hmac.compare_digest(self.headers.get('X-Session-Token', ''), token):
                return self.send({'error': '会话已失效，请刷新页面'}, 403)
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
                return self.send({'error': '请求来源无效'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError('请求大小无效')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('请求格式无效')
                route = urlparse(self.path).path
                if route == '/api/scan':
                    result = library.scan(payload.get('folder', ''), bool(payload.get('recursive', True)))
                elif route == '/api/classify':
                    result = library.classify()
                elif route == '/api/search':
                    result = library.search(payload.get('query', ''))
                elif route == '/api/config':
                    result = library.save_config(payload)
                elif route == '/api/review':
                    result = library.review(payload.get('ids'), payload.get('status'))
                elif route == '/api/export':
                    result = library.export(payload)
                elif route == '/api/cancel':
                    library.cancel_event.set()
                    result = {'ok': True}
                elif route == '/api/regroup':
                    result = library.start('duplicates', library.regroup)
                elif route == '/api/folders':
                    result = folders.browse(payload.get('path', ''), payload.get('query', ''),
                                            payload.get('offset', 0), payload.get('limit', 200))
                elif route == '/api/folders/select':
                    result = folders.remember(payload.get('path', ''))
                else:
                    return self.send({'error': '接口不存在'}, 404)
                self.send(result)
            except (ValueError, TypeError, KeyError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception as exc:
                self.send({'error': str(exc)}, 500)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='拾光 · 本地照片整理')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=ROOT / '.data')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    library = Library(args.data)
    try:
        server = make_server(library, args.port)
    except OSError:
        print(f'端口 {args.port} 已被占用。请检查已有窗口，或使用 --port 8766。')
        return 1
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'拾光已启动：{url}\nPython：{sys.executable}\n素材仅在本机处理。关闭此窗口或按 Ctrl+C 停止服务。', flush=True)
    if not args.no_browser:
        threading.Timer(.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        library.cancel_event.set()
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
