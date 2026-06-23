from __future__ import annotations

import base64
import os
import select
import socket
import socketserver
import subprocess
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import app_root, bundled_root
from .models import AppSettings, TargetSite


BLOCK_STATUSES = {401, 403, 407, 418, 429, 451, 503}
CAPTCHA_HINTS = (
    "captcha",
    "robot check",
    "are you human",
    "verify you are human",
    "unusual traffic",
    "access denied",
)

_KEPT_LOCAL_CHROME_SESSIONS: list[dict[str, Any]] = []
TUNNEL_IDLE_SECONDS = 3600


@dataclass
class BrowserCheck:
    reachable: bool
    target_reachable: bool
    latency_ms: int | None
    status_code: int | None
    final_url: str
    title: str
    blocked: bool
    captcha: bool
    notes: list[str]
    tags: list[str]


def run_browser_target_check(
    proxy_server: str,
    proxy_username: str,
    proxy_password: str,
    target: TargetSite,
    settings: AppSettings,
) -> BrowserCheck:
    bundled_browsers = bundled_root() / "ms-playwright"
    if bundled_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)

    if settings.local_chrome_test:
        return run_local_chrome_target_check(
            proxy_server=proxy_server,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            target=target,
            settings=settings,
        )

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return BrowserCheck(
            reachable=False,
            target_reachable=False,
            latency_ms=None,
            status_code=None,
            final_url=target.url,
            title="",
            blocked=False,
            captcha=False,
            notes=["Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"],
            tags=["browser-missing"],
        )

    playwright_manager = None
    browser = None
    context = None
    local_bridge = None
    try:
        playwright_manager = sync_playwright().start()
        proxy_config = {
            "server": proxy_server,
            "username": proxy_username,
            "password": proxy_password,
        }
        browser = playwright_manager.chromium.launch(
            headless=True,
            proxy=proxy_config,
        )
        context = browser.new_context(user_agent=settings.user_agent)
        page = context.new_page()
        started = time.perf_counter()
        response = page.goto(
            target.url,
            timeout=settings.timeout_seconds * 1000,
            wait_until="domcontentloaded",
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        status_code = response.status if response else None
        title = page.title()
        body_sample = page.content()[:20000].lower()
        final_url = page.url
        blocked = bool(status_code in BLOCK_STATUSES) or "access denied" in body_sample
        captcha = any(hint in body_sample for hint in CAPTCHA_HINTS)

        notes = [
            f"Browser target returned HTTP {status_code if status_code is not None else 'unknown'}.",
            f"Browser final URL: {final_url}",
        ]
        if title:
            notes.append(f"Browser page title: {title}")

        tags: list[str] = ["browser"]
        if blocked:
            tags.append("blocked")
        if captcha:
            tags.append("captcha")

        return BrowserCheck(
            reachable=True,
            target_reachable=bool(status_code is None or status_code < 500),
            latency_ms=latency_ms,
            status_code=status_code,
            final_url=final_url,
            title=title,
            blocked=blocked,
            captcha=captcha,
            notes=notes,
            tags=tags,
        )
    except PlaywrightTimeoutError:
        if settings.local_chrome_test and context is not None and playwright_manager is not None:
            keep_local_chrome_session(
                playwright_manager=playwright_manager,
                context=context,
                profile_dir=str(profile_dir) if "profile_dir" in locals() else "",
                local_bridge=local_bridge,
                local_proxy=proxy_arg if "proxy_arg" in locals() else "",
                target_url=target.url,
            )
            playwright_manager = None
            context = None
            local_bridge = None
            return BrowserCheck(
                reachable=False,
                target_reachable=False,
                latency_ms=None,
                status_code=None,
                final_url=target.url,
                title="",
                blocked=False,
                captcha=False,
                notes=[
                    "Browser navigation timed out.",
                    "Local Chrome remains open with the current local proxy bridge for manual testing.",
                ],
                tags=["browser-timeout", "local-chrome", "local-proxy-bridge"],
            )
        return BrowserCheck(
            reachable=False,
            target_reachable=False,
            latency_ms=None,
            status_code=None,
            final_url=target.url,
            title="",
            blocked=False,
            captcha=False,
            notes=["Browser navigation timed out."],
            tags=["browser-timeout"],
        )
    except PlaywrightError as exc:
        if settings.local_chrome_test and context is not None and playwright_manager is not None:
            keep_local_chrome_session(
                playwright_manager=playwright_manager,
                context=context,
                profile_dir=str(profile_dir) if "profile_dir" in locals() else "",
                local_bridge=local_bridge,
                local_proxy=proxy_arg if "proxy_arg" in locals() else "",
                target_url=target.url,
            )
            playwright_manager = None
            context = None
            local_bridge = None
            return BrowserCheck(
                reachable=False,
                target_reachable=False,
                latency_ms=None,
                status_code=None,
                final_url=target.url,
                title="",
                blocked=False,
                captcha=False,
                notes=[
                    f"Browser error: {exc}",
                    "Local Chrome remains open with the current local proxy bridge for manual testing.",
                ],
                tags=["browser-error", "local-chrome", "local-proxy-bridge"],
            )
        return BrowserCheck(
            reachable=False,
            target_reachable=False,
            latency_ms=None,
            status_code=None,
            final_url=target.url,
            title="",
            blocked=False,
            captcha=False,
            notes=[f"Browser error: {exc}"],
            tags=["browser-error"],
        )
    except Exception as exc:  # noqa: BLE001
        if settings.local_chrome_test and context is not None and playwright_manager is not None:
            keep_local_chrome_session(
                playwright_manager=playwright_manager,
                context=context,
                profile_dir=str(profile_dir) if "profile_dir" in locals() else "",
                local_bridge=local_bridge,
                local_proxy=proxy_arg if "proxy_arg" in locals() else "",
                target_url=target.url,
            )
            playwright_manager = None
            context = None
            local_bridge = None
            return BrowserCheck(
                reachable=False,
                target_reachable=False,
                latency_ms=None,
                status_code=None,
                final_url=target.url,
                title="",
                blocked=False,
                captcha=False,
                notes=[
                    f"Unexpected browser error: {exc}",
                    "Local Chrome remains open with the current local proxy bridge for manual testing.",
                ],
                tags=["browser-error", "local-chrome", "local-proxy-bridge"],
            )
        return BrowserCheck(
            reachable=False,
            target_reachable=False,
            latency_ms=None,
            status_code=None,
            final_url=target.url,
            title="",
            blocked=False,
            captcha=False,
            notes=[f"Unexpected browser error: {exc}"],
            tags=["browser-error"],
        )
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if local_bridge is not None:
            local_bridge.stop()
        if playwright_manager is not None:
            try:
                playwright_manager.stop()
            except Exception:
                pass


def run_local_chrome_target_check(
    proxy_server: str,
    proxy_username: str,
    proxy_password: str,
    target: TargetSite,
    settings: AppSettings,
) -> BrowserCheck:
    profile_dir = local_chrome_profile_dir()
    local_bridge = None
    chrome_process = None
    proxy_arg = ""
    try:
        chrome_path = find_chrome_executable()
        if chrome_path is None:
            return BrowserCheck(
                reachable=False,
                target_reachable=False,
                latency_ms=None,
                status_code=None,
                final_url=target.url,
                title="",
                blocked=False,
                captcha=False,
                notes=["Local Chrome executable was not found."],
                tags=["chrome-missing"],
            )

        local_bridge = LocalProxyBridge(
            proxy_server=proxy_server,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
        )
        local_bridge.start()
        proxy_arg = f"http://127.0.0.1:{local_bridge.port}"
        chrome_proxy_arg = chrome_fixed_proxy_arg(int(local_bridge.port))
        profile_dir.mkdir(parents=True, exist_ok=True)

        chrome_args = [
            str(chrome_path),
            f"--user-data-dir={profile_dir}",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-quic",
            f"--proxy-server={chrome_proxy_arg}",
            "--proxy-bypass-list=<-loopback>",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            target.url,
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        chrome_process = subprocess.Popen(  # noqa: S603
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        started = time.perf_counter()
        status_code = None
        final_url = target.url
        body_sample = ""
        notes = [
            "Local Chrome visible test was enabled. Chrome will remain open after the audit.",
            f"Chrome PID: {chrome_process.pid}",
            f"Chrome is bound to local proxy bridge {proxy_arg}, which forwards all tabs through the current proxy.",
        ]
        try:
            with httpx.Client(
                proxy=proxy_arg,
                timeout=settings.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": settings.user_agent},
            ) as client:
                response = client.get(target.url)
                status_code = response.status_code
                final_url = str(response.url)
                body_sample = response.text[:20000].lower()
                notes.append(f"Local proxy bridge probe returned HTTP {status_code}.")
                notes.append(f"Probe final URL: {final_url}")
        except httpx.TimeoutException:
            notes.append("Local proxy bridge probe timed out.")
        except httpx.HTTPError as exc:
            notes.append(f"Local proxy bridge probe error: {exc}")

        latency_ms = int((time.perf_counter() - started) * 1000)
        blocked = bool(status_code in BLOCK_STATUSES) or "access denied" in body_sample
        captcha = any(hint in body_sample for hint in CAPTCHA_HINTS)
        tags = ["browser", "local-chrome", "local-proxy-bridge"]
        if blocked:
            tags.append("blocked")
        if captcha:
            tags.append("captcha")

        _KEPT_LOCAL_CHROME_SESSIONS.append(
            {
                "process": chrome_process,
                "profile_dir": str(profile_dir),
                "local_bridge": local_bridge,
                "local_proxy": proxy_arg,
                "target_url": target.url,
            }
        )
        local_bridge = None
        chrome_process = None

        return BrowserCheck(
            reachable=status_code is not None,
            target_reachable=bool(status_code is not None and status_code < 500),
            latency_ms=latency_ms if status_code is not None else None,
            status_code=status_code,
            final_url=final_url,
            title="",
            blocked=blocked,
            captcha=captcha,
            notes=notes,
            tags=tags,
        )
    except Exception as exc:  # noqa: BLE001
        return BrowserCheck(
            reachable=False,
            target_reachable=False,
            latency_ms=None,
            status_code=None,
            final_url=target.url,
            title="",
            blocked=False,
            captcha=False,
            notes=[f"Local Chrome launch error: {exc}"],
            tags=["browser-error", "local-chrome"],
        )
    finally:
        if chrome_process is not None:
            try:
                chrome_process.terminate()
            except OSError:
                pass
        if local_bridge is not None:
            local_bridge.stop()


def find_chrome_executable() -> Path | None:
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def local_chrome_profile_dir():
    profiles_root = app_root() / "config" / "chrome-profiles"
    profiles_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return profiles_root / f"proxy-session-{stamp}-{uuid.uuid4().hex[:8]}"


def keep_local_chrome_session(
    playwright_manager: Any,
    context: Any,
    profile_dir: str,
    local_bridge: Any,
    local_proxy: str,
    target_url: str,
) -> None:
    _KEPT_LOCAL_CHROME_SESSIONS.append(
        {
            "playwright": playwright_manager,
            "context": context,
            "page": context.pages[0] if context.pages else None,
            "profile_dir": profile_dir,
            "local_bridge": local_bridge,
            "local_proxy": local_proxy,
            "target_url": target_url,
        }
    )


class LocalProxyBridge:
    def __init__(self, proxy_server: str, proxy_username: str, proxy_password: str) -> None:
        parsed = urlparse(proxy_server)
        self.upstream_scheme = parsed.scheme or "http"
        self.upstream_host = parsed.hostname
        self.upstream_port = parsed.port
        if not self.upstream_host or not self.upstream_port:
            raise ValueError(f"Invalid proxy server for local Chrome: {proxy_server}")
        token = f"{proxy_username}:{proxy_password}".encode("utf-8")
        self.auth_header = "Proxy-Authorization: Basic " + base64.b64encode(token).decode("ascii")
        self.server: socketserver.ThreadingTCPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self.active_connections = 0

    def start(self) -> None:
        bridge = self

        class BridgeServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            request_queue_size = 128

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                bridge.handle_client(self.request)

        self.server = BridgeServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def handle_client(self, client: socket.socket) -> None:
        upstream = None
        self.active_connections += 1
        try:
            configure_socket(client)
            header, rest = read_http_header(client)
            if not header:
                return
            first_line = header.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
            parts = first_line.split(" ")
            if len(parts) < 3:
                return
            method, target, version = parts[0].upper(), parts[1], parts[2]
            try:
                upstream = self.open_upstream()
            except OSError as exc:
                send_proxy_bridge_error(client, f"Upstream proxy connection failed: {exc}")
                return
            if method == "CONNECT":
                request = (
                    f"CONNECT {target} {version}\r\n"
                    f"Host: {target}\r\n"
                    f"{self.auth_header}\r\n"
                    "Proxy-Connection: keep-alive\r\n\r\n"
                ).encode("iso-8859-1")
                upstream.sendall(request)
                response_header, response_rest = read_http_header(upstream)
                client.sendall(response_header + response_rest)
                status_line = response_header.split(b"\r\n", 1)[0]
                if b" 200 " in status_line:
                    tunnel(client, upstream)
                return

            upstream.sendall(inject_proxy_auth(header, self.auth_header) + rest)
            tunnel(client, upstream)
        except OSError:
            return
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass
            self.active_connections = max(0, self.active_connections - 1)

    def open_upstream(self) -> socket.socket:
        raw = socket.create_connection((self.upstream_host, int(self.upstream_port)), timeout=20)
        configure_socket(raw)
        if self.upstream_scheme == "https":
            wrapped = ssl.create_default_context().wrap_socket(raw, server_hostname=self.upstream_host)
            configure_socket(wrapped)
            return wrapped
        return raw


def read_http_header(sock: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 1024 * 1024:
            break
    marker = data.find(b"\r\n\r\n")
    if marker < 0:
        return data, b""
    end = marker + 4
    return data[:end], data[end:]


def configure_socket(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def send_proxy_bridge_error(client: socket.socket, message: str) -> None:
    body = message.encode("utf-8", errors="replace")
    response = (
        b"HTTP/1.1 502 Bad Gateway\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + body
    )
    try:
        client.sendall(response)
    except OSError:
        pass


def inject_proxy_auth(header: bytes, auth_header: str) -> bytes:
    text = header.decode("iso-8859-1", errors="replace")
    lines = text.split("\r\n")
    output = [lines[0]]
    inserted = False
    for line in lines[1:]:
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("proxy-authorization:"):
            if not inserted:
                output.append(auth_header)
                inserted = True
            continue
        if lower.startswith("proxy-connection:"):
            continue
        output.append(line)
    if not inserted:
        output.append(auth_header)
    output.append("Proxy-Connection: keep-alive")
    return ("\r\n".join(output) + "\r\n\r\n").encode("iso-8859-1")


def tunnel(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    while True:
        readable, _, errored = select.select(sockets, [], sockets, TUNNEL_IDLE_SECONDS)
        if errored:
            return
        if not readable:
            continue
        for source in readable:
            target = right if source is left else left
            data = source.recv(65536)
            if not data:
                return
            target.sendall(data)


def chrome_fixed_proxy_arg(local_port: int) -> str:
    local_proxy = f"127.0.0.1:{local_port}"
    return f"http={local_proxy};https={local_proxy}"
