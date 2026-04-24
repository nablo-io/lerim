"""Auth commands for Lerim Cloud: login, status, logout.

Implements browser-based OAuth callback flow and manual token entry.
The token is persisted in ~/.lerim/config.toml under [cloud].
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from lerim.config.settings import get_config, save_config_patch


def _emit(message: object = "", *, file: Any | None = None) -> None:
    """Write one CLI output line to stdout or a provided file-like target."""
    target = file if file is not None else sys.stdout
    target.write(f"{message}\n")


# ---------------------------------------------------------------------------
# Callback server
# ---------------------------------------------------------------------------


class _TokenCallbackServer(HTTPServer):
    """Local callback server that owns one browser-flow token result."""

    token_result: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-use HTTP handler that captures a token from ``/callback?token=...``."""

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        token_list = params.get("token", [])
        if not token_list or not token_list[0].strip():
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing token parameter")
            return

        setattr(self.server, "token_result", token_list[0].strip())
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Authenticated successfully.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, _format: str, *_args: Any) -> None:
        """Suppress default stderr logging from BaseHTTPRequestHandler."""


def _find_available_port() -> int:
    """Find an available port in the 9876-9899 range, falling back to OS assignment."""
    ports = list(range(9876, 9900))
    random.shuffle(ports)
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    # Fallback: let OS pick
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_browser_flow(endpoint: str, timeout_seconds: int = 120) -> str | None:
    """Start a localhost callback server, open the browser, and wait for a token.

    Returns the token string on success, or None on timeout.
    """
    port = _find_available_port()
    server = _TokenCallbackServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1.0

    callback_url = f"http://localhost:{port}/callback"
    auth_url = f"{endpoint}/auth/cli?callback={callback_url}"

    _emit(f"Opening browser to: {auth_url}")
    webbrowser.open(auth_url)
    _emit("Waiting for authentication callback...")

    # Run the server in a thread so we can enforce a timeout
    stop = threading.Event()

    def _serve() -> None:
        import time

        deadline = time.monotonic() + timeout_seconds
        while not stop.is_set() and time.monotonic() < deadline:
            server.handle_request()
            if server.token_result is not None:
                break

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    stop.set()
    server.server_close()

    return server.token_result


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------


def cmd_auth(args: argparse.Namespace) -> int:
    """Handle ``lerim auth`` (login) — browser flow or manual --token."""
    config = get_config()
    endpoint = config.cloud_endpoint.rstrip("/")

    # Manual token entry
    manual_token: str | None = getattr(args, "token", None)
    if manual_token:
        manual_token = manual_token.strip()
        if not manual_token:
            _emit("Token cannot be empty.", file=sys.stderr)
            return 1
        save_config_patch({"cloud": {"token": manual_token}})
        _emit("Authenticated successfully.")
        return 0

    # Browser-based flow
    token = _run_browser_flow(endpoint)
    if token is None:
        _emit(
            "Authentication timed out. No callback received within 120 seconds.",
            file=sys.stderr,
        )
        _emit(
            "You can authenticate manually with: lerim auth --token <token>",
            file=sys.stderr,
        )
        return 1

    save_config_patch({"cloud": {"token": token}})
    _emit("Authenticated successfully.")
    return 0


def cmd_auth_status(args: argparse.Namespace) -> int:
    """Handle ``lerim auth status`` — check token and verify with cloud."""
    config = get_config()
    token = config.cloud_token

    if not token:
        _emit("Not authenticated. Run `lerim auth` to log in.")
        return 0

    endpoint = config.cloud_endpoint.rstrip("/")
    verify_url = f"{endpoint}/api/v1/auth/me"

    try:
        req = urllib.request.Request(
            verify_url,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            display = data.get("email") or data.get("name") or "unknown"
            _emit(f"Authenticated as {display}")
            return 0
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            _emit("Token found but could not verify (cloud may be unreachable)")
            return 0
        _emit("Token found but could not verify (cloud may be unreachable)")
        return 0
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        _emit("Token found but could not verify (cloud may be unreachable)")
        return 0


def cmd_auth_logout(args: argparse.Namespace) -> int:
    """Handle ``lerim auth logout`` — remove token from config."""
    save_config_patch({"cloud": {"token": ""}})
    _emit("Logged out successfully.")
    return 0
