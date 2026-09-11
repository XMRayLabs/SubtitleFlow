"""转录服务的 HTTP 接口。只监听回环地址，所有请求都要带启动时生成的令牌。"""
from dataclasses import asdict
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re

from . import API_VERSION

JOB_PATH = re.compile(r"^/v1/jobs/([0-9a-f]{32})(/result|/cancel)?$")
MAX_BODY = 64 * 1024


class Server(ThreadingHTTPServer):
    daemon_threads = True


def create_server(service, token: str, host="127.0.0.1", port=0) -> Server:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            given = self.headers.get("Authorization", "")
            if hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
                return True
            self.reply(401, {"error": "未授权"})
            return False

        def body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("请求长度无效")
            # 负数长度会让 rfile.read 一直读到连接关闭，未经鉴权就能占住处理线程
            if not 0 <= length <= MAX_BODY:
                raise ValueError("请求长度无效或过大")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("请求格式错误")
            return data

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == "/v1/health":
                return self.reply(200, {"api_version": API_VERSION, **service.health()})
            match = JOB_PATH.match(self.path)
            job = service.get(match.group(1)) if match and match.group(2) != "/cancel" else None
            if not job:
                return self.reply(404, {"error": "任务不存在"})
            if match.group(2) == "/result":
                if job.status != "done":
                    return self.reply(409, {"error": "任务尚未完成"})
                return self.reply(200, {"cues": [asdict(cue) for cue in job.cues]})
            self.reply(200, job.snapshot())

        def do_POST(self):
            # 先读完请求体：Windows 上关闭仍有未读数据的连接会发送 RST，客户端收不到响应
            try:
                data = self.body()
            except ValueError as exc:
                return self.reply(400, {"error": str(exc)})
            if not self.authorized():
                return
            try:
                if self.path == "/v1/jobs":
                    job = service.submit(data.get("path"), data.get("segment_seconds"))
                    return self.reply(201, job.snapshot())
            except ValueError as exc:
                return self.reply(400, {"error": str(exc)})
            match = JOB_PATH.match(self.path)
            if match and match.group(2) == "/cancel":
                job = service.cancel(match.group(1))
                return self.reply(200, job.snapshot()) if job else self.reply(404, {"error": "任务不存在"})
            self.reply(404, {"error": "接口不存在"})

    return Server((host, port), Handler)
