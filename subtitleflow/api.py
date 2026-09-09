"""Minimal OpenAI-compatible HTTP client. No provider-specific SDK."""
from dataclasses import dataclass, field, replace
import json
import ipaddress
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from .safety import response_json


class Cancelled(Exception):
    pass


class APIError(Exception):
    pass


class TooLarge(APIError):
    pass


class InvalidResponse(APIError):
    pass


@dataclass(frozen=True)
class APIConfig:
    base_url: str
    key: str = field(repr=False)
    model: str = ""
    timeout: float = 60.0

    allow_local_self_signed: bool = False

    def base(self):
        url = self.base_url.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API 地址必须是有效的 HTTP(S) Base URL，不能包含账号、查询参数或片段")
        if self.allow_local_self_signed and not self.is_loopback():
            raise ValueError("跳过证书验证仅允许 localhost 或回环 IP 地址")
        if url.endswith("/chat/completions"):
            url = url[:-len("/chat/completions")]
        if not urllib.parse.urlsplit(url).path.rstrip("/"):
            url += "/v1"
        return url

    def is_loopback(self):
        host = urllib.parse.urlsplit(self.base_url.strip()).hostname
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def endpoint(self):
        base = self.base()
        if not self.model.strip():
            raise ValueError("请选择或填写模型名称")
        return base + "/chat/completions"

    def models_endpoint(self):
        return self.base() + "/models"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config: APIConfig, cancel: threading.Event):
        self.config, self.cancel = config, cancel

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("任务已取消")

    def opener(self):
        handlers = [NoRedirect()]
        if self.config.is_loopback():
            handlers.append(urllib.request.ProxyHandler({}))
        if self.config.allow_local_self_signed:
            self.config.base()  # Reject non-loopback hosts before changing TLS behavior.
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        return urllib.request.build_opener(*handlers)

    def connection_error(self, exc):
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, ssl.SSLCertVerificationError):
            suffix = "；若是你自己的本地服务，可勾选“允许本机自签名证书”" if self.config.is_loopback() else ""
            return APIError("HTTPS 证书验证失败，请配置受信任证书" + suffix)
        if isinstance(reason, ssl.SSLError):
            return APIError("TLS 握手失败，请确认服务使用 HTTPS 还是 HTTP")
        if isinstance(reason, ConnectionRefusedError):
            return APIError("连接被拒绝，请检查服务是否启动以及端口是否正确")
        return APIError("无法连接 API，请检查地址、端口和网络（请求超时或连接中断）")

    def discover_base(self):
        """Probe only a loopback root, without forwarding a credential."""
        base = self.config.base()
        raw = self.config.base_url.strip().rstrip("/")
        if not self.config.is_loopback() or urllib.parse.urlsplit(raw).path.rstrip("/"):
            return base
        request = urllib.request.Request(raw + "/openapi.json", headers={"Accept": "application/json"})
        try:
            with self.opener().open(request, timeout=8) as response:
                schema = response_json(response, self.cancel)
            paths = schema.get("paths", {}) if isinstance(schema, dict) else {}
            if "/api/models" in paths and "/api/chat/completions" in paths:
                return raw + "/api"
        except urllib.error.HTTPError as exc:
            exc.close()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(reason, ssl.SSLError):
                raise self.connection_error(exc) from None
        except ValueError:
            pass
        return base

    def models(self):
        self.check()
        url = self.config.models_endpoint()
        headers = {"Accept": "application/json"}
        if self.config.key:
            headers["Authorization"] = "Bearer " + self.config.key
        try:
            request = urllib.request.Request(url, headers=headers)
            with self.opener().open(request, timeout=min(self.config.timeout, 20)) as response:
                data = response_json(response, self.cancel)
            self.check()
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise APIError("模型列表认证失败，请检查 API Key") from None
            raise APIError(f"模型列表请求失败（HTTP {code}），请检查 Base URL；也可手动填写模型") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise self.connection_error(exc) from None
        except (ValueError, TypeError):
            raise InvalidResponse("模型接口未返回 JSON，请检查 API 路径是否正确") from None
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise InvalidResponse("模型接口应返回包含 data 数组的兼容响应")
        if len(data["data"]) > 5000:
            raise InvalidResponse("模型列表超过 5000 项上限")
        ids = []
        for item in data["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise InvalidResponse("模型列表缺少有效模型 ID")
            if item["id"] not in ids:
                ids.append(item["id"])
        if not ids:
            raise InvalidResponse("服务返回空模型列表，可手动填写模型名称")
        return ids

    def chat(self, system: str, content: str):
        raw = self.config.base_url.strip().rstrip("/")
        if self.config.is_loopback() and not urllib.parse.urlsplit(raw).path.rstrip("/"):
            self.config = replace(self.config, base_url=self.discover_base())
        url = self.config.endpoint()
        payload = json.dumps({"model": self.config.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": content}
        ]}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.key:
            headers["Authorization"] = "Bearer " + self.config.key
        for attempt in range(4):
            self.check()
            try:
                request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with self.opener().open(request, timeout=self.config.timeout) as response:
                    data = response_json(response, self.cancel)
                self.check()
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise TooLarge("模型输出被截断")
                value = choice["message"]["content"]
                if not isinstance(value, str) or not value.strip():
                    raise InvalidResponse("模型没有返回正文")
                return value
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry_after = exc.headers.get('Retry-After', '')
                # Error bodies may contain credentials or reflected prompts; never log them.
                body = exc.read(16384).decode("utf-8", errors="replace").lower()
                exc.close()
                if code == 413 or (code == 400 and any(word in body for word in ("context_length", "maximum context", "too many tokens", "context window"))):
                    raise TooLarge("请求超过模型上下文限制") from None
                if code in (401, 403):
                    raise APIError("API 认证或权限失败，请检查密钥和模型权限") from None
                if code not in (408, 429, 500, 502, 503, 504):
                    raise APIError(f"API 请求失败（HTTP {code}），请检查地址与模型") from None

                delay = min(float(retry_after), 60) if retry_after.isdigit() else 2 ** attempt
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                if isinstance(reason, (ssl.SSLError, ConnectionRefusedError)) or attempt == 3:
                    raise self.connection_error(exc) from None
                delay = 2 ** attempt
            except (KeyError, IndexError, TypeError, ValueError):
                raise InvalidResponse("API 响应不是兼容的聊天结果") from None
            if attempt == 3:
                raise APIError("API 临时故障，重试 3 次后仍未成功") from None
            if self.cancel.wait(delay):
                raise Cancelled("任务已取消")

    def json_chat(self, system: str, content: str):
        raw = self.chat(system, content).strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1])
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            raise InvalidResponse("模型返回了无效 JSON") from None

    def test(self):
        return self.chat("Reply with OK.", "Connection test")
