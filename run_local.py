import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_env()

from lambda_function import handler  # noqa: E402  (must come after env load)


class _Handler(BaseHTTPRequestHandler):
    def _dispatch(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        query_params = {k: v[0] if len(v) == 1 else v for k, v in qs.items()} or None

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else None

        event = {
            "httpMethod": self.command,
            "path": parsed.path,
            "queryStringParameters": query_params,
            "headers": dict(self.headers),
            "body": body,
            "isBase64Encoded": False,
            "requestContext": {},
        }

        result = handler(event, {})

        status = result.get("statusCode", 200)
        resp_body = result.get("body", "{}").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (result.get("headers") or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp_body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _dispatch

    def log_message(self, fmt, *args):
        print(f"  {self.command:6} {self.path}  →  {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Local server -> http://localhost:{port}")
    HTTPServer(("0.0.0.0", port), _Handler).serve_forever()
