from http.server import BaseHTTPRequestHandler, HTTPServer
import json

profile = {
    "heroTitle": "手搓API",
    "heroSubtitle": "2026.10.11",
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/profile":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps(profile, ensure_ascii=False)  # ensure_ascii=False：让中文原样输出
            self.wfile.write(body.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

print("后端已启动：http://localhost:8000/api/profile")
HTTPServer(("", 8000), Handler).serve_forever()
