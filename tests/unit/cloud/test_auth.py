"""Unit tests for cloud auth commands: login, status, logout.

Covers cmd_auth (manual token + browser flow), cmd_auth_status (verify
with cloud, network errors, HTTP errors, missing token), cmd_auth_logout,
the _CallbackHandler HTTP handler, _find_available_port, and _emit.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import urllib.error
import urllib.parse
from unittest.mock import MagicMock


from lerim.cloud import auth as auth_mod
from lerim.cloud.auth import (
	_CallbackHandler,
	_emit,
	_find_available_port,
	_run_browser_flow,
	cmd_auth,
	cmd_auth_logout,
	cmd_auth_status,
)


# ---------------------------------------------------------------------------
# _emit
# ---------------------------------------------------------------------------


def test_emit_writes_to_stdout(capsys) -> None:
	"""_emit writes a newline-terminated line to stdout by default."""
	_emit("hello world")
	captured = capsys.readouterr()
	assert captured.out == "hello world\n"


def test_emit_writes_to_custom_file() -> None:
	"""_emit writes to a provided file-like object when given."""
	buf = io.StringIO()
	_emit("custom target", file=buf)
	assert buf.getvalue() == "custom target\n"


def test_emit_empty_message(capsys) -> None:
	"""_emit with no arguments writes an empty line."""
	_emit()
	captured = capsys.readouterr()
	assert captured.out == "\n"


# ---------------------------------------------------------------------------
# _find_available_port
# ---------------------------------------------------------------------------


def test_find_available_port_returns_int(monkeypatch) -> None:
	"""_find_available_port returns a valid port number."""
	class FakeSocket:
		"""Socket double that accepts the first requested bind."""

		def bind(self, address):
			"""Accept the requested address."""
			self.address = address

		def __enter__(self):
			"""Context manager enter."""
			return self

		def __exit__(self, *args):
			"""Context manager exit."""

	monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FakeSocket())
	port = _find_available_port()
	assert isinstance(port, int)
	assert port > 0


def test_find_available_port_fallback(monkeypatch) -> None:
	"""When all preferred ports are taken, falls back to OS-assigned port."""
	call_count = 0

	class FakeSocket:
		"""Socket that refuses bind on preferred ports."""

		def bind(self, address):
			"""Reject preferred-range ports, accept OS-assigned (port 0)."""
			nonlocal call_count
			call_count += 1
			_, port = address
			if port != 0:
				raise OSError("port busy")
			self.address = address

		def getsockname(self):
			"""Return a deterministic OS-assigned port."""
			return ("127.0.0.1", 43210)

		def __enter__(self):
			"""Context manager enter."""
			return self

		def __exit__(self, *args):
			"""Context manager exit."""

	monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FakeSocket())
	port = _find_available_port()
	assert isinstance(port, int)
	assert port > 0


# ---------------------------------------------------------------------------
# _CallbackHandler
# ---------------------------------------------------------------------------


def test_callback_handler_valid_token() -> None:
	"""GET /callback?token=abc sets server token_result and returns 200."""

	handler = MagicMock(spec=_CallbackHandler)
	handler.path = "/callback?token=test-token-123"
	handler.server = MagicMock(token_result=None)
	handler.wfile = io.BytesIO()
	handler.send_response = MagicMock()
	handler.send_header = MagicMock()
	handler.end_headers = MagicMock()

	_CallbackHandler.do_GET(handler)

	assert handler.server.token_result == "test-token-123"
	handler.send_response.assert_called_with(200)


def test_callback_handler_missing_token() -> None:
	"""GET /callback without token parameter returns 400."""

	handler = MagicMock(spec=_CallbackHandler)
	handler.path = "/callback"
	handler.server = MagicMock(token_result=None)
	handler.wfile = io.BytesIO()
	handler.send_response = MagicMock()
	handler.end_headers = MagicMock()

	_CallbackHandler.do_GET(handler)

	assert handler.server.token_result is None
	handler.send_response.assert_called_with(400)


def test_callback_handler_wrong_path() -> None:
	"""GET on a non-/callback path returns 404."""
	handler = MagicMock(spec=_CallbackHandler)
	handler.path = "/other"
	handler.wfile = io.BytesIO()
	handler.send_response = MagicMock()
	handler.end_headers = MagicMock()

	_CallbackHandler.do_GET(handler)

	handler.send_response.assert_called_with(404)


def test_callback_handler_empty_token() -> None:
	"""GET /callback?token= (empty) returns 400."""

	handler = MagicMock(spec=_CallbackHandler)
	handler.path = "/callback?token="
	handler.server = MagicMock(token_result=None)
	handler.wfile = io.BytesIO()
	handler.send_response = MagicMock()
	handler.end_headers = MagicMock()

	_CallbackHandler.do_GET(handler)

	assert handler.server.token_result is None
	handler.send_response.assert_called_with(400)


def test_callback_handler_log_message_suppressed() -> None:
	"""log_message does nothing (no stderr output)."""
	handler = MagicMock(spec=_CallbackHandler)
	# Should not raise
	_CallbackHandler.log_message(handler, "test %s", "arg")


# ---------------------------------------------------------------------------
# cmd_auth — manual token
# ---------------------------------------------------------------------------


def test_cmd_auth_manual_token(monkeypatch) -> None:
	"""cmd_auth with --token saves token and returns 0."""
	saved: list[dict] = []
	monkeypatch.setattr(
		auth_mod, "save_config_patch", lambda patch: saved.append(patch)
	)
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_endpoint="https://api.lerim.dev"),
	)

	args = argparse.Namespace(token="my-secret-token")
	result = cmd_auth(args)

	assert result == 0
	assert saved[0] == {"cloud": {"token": "my-secret-token"}}


def test_cmd_auth_manual_token_stripped(monkeypatch) -> None:
	"""cmd_auth strips whitespace from manual token."""
	saved: list[dict] = []
	monkeypatch.setattr(
		auth_mod, "save_config_patch", lambda patch: saved.append(patch)
	)
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_endpoint="https://api.lerim.dev"),
	)

	args = argparse.Namespace(token="  spaced-token  ")
	result = cmd_auth(args)

	assert result == 0
	assert saved[0]["cloud"]["token"] == "spaced-token"


def test_cmd_auth_manual_empty_token(monkeypatch, capsys) -> None:
	"""cmd_auth with blank token returns error 1."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_endpoint="https://api.lerim.dev"),
	)

	args = argparse.Namespace(token="   ")
	result = cmd_auth(args)

	assert result == 1


def test_cmd_auth_browser_flow_success(monkeypatch) -> None:
	"""cmd_auth without --token runs browser flow and saves returned token."""
	saved: list[dict] = []
	monkeypatch.setattr(
		auth_mod, "save_config_patch", lambda patch: saved.append(patch)
	)
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_endpoint="https://api.lerim.dev"),
	)
	monkeypatch.setattr(auth_mod, "_run_browser_flow", lambda endpoint: "browser-token")

	args = argparse.Namespace(token=None)
	result = cmd_auth(args)

	assert result == 0
	assert saved[0]["cloud"]["token"] == "browser-token"


def test_cmd_auth_browser_flow_timeout(monkeypatch) -> None:
	"""cmd_auth returns 1 when browser flow times out (returns None)."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_endpoint="https://api.lerim.dev"),
	)
	monkeypatch.setattr(auth_mod, "_run_browser_flow", lambda endpoint: None)

	args = argparse.Namespace(token=None)
	result = cmd_auth(args)

	assert result == 1


def test_cmd_auth_no_token_attr(monkeypatch) -> None:
	"""cmd_auth with Namespace missing token attribute runs browser flow."""
	saved: list[dict] = []
	monkeypatch.setattr(
		auth_mod, "save_config_patch", lambda patch: saved.append(patch)
	)
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_endpoint="https://api.lerim.dev"),
	)
	monkeypatch.setattr(auth_mod, "_run_browser_flow", lambda endpoint: "flow-token")

	args = argparse.Namespace()  # no token attribute
	result = cmd_auth(args)

	assert result == 0
	assert saved[0]["cloud"]["token"] == "flow-token"


# ---------------------------------------------------------------------------
# cmd_auth_status
# ---------------------------------------------------------------------------


def test_cmd_auth_status_not_authenticated(monkeypatch, capsys) -> None:
	"""cmd_auth_status with no token reports not authenticated."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(cloud_token=None, cloud_endpoint="https://api.lerim.dev"),
	)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "Not authenticated" in out


def test_cmd_auth_status_verified(monkeypatch, capsys) -> None:
	"""cmd_auth_status with valid token shows user email."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="valid-tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	response_data = json.dumps({"email": "user@example.com"}).encode()
	mock_resp = MagicMock()
	mock_resp.read.return_value = response_data
	mock_resp.__enter__ = lambda s: s
	mock_resp.__exit__ = MagicMock(return_value=False)

	monkeypatch.setattr(
		urllib.request, "urlopen",
		lambda req, **kw: mock_resp,
	)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "user@example.com" in out


def test_cmd_auth_status_falls_back_to_localhost(monkeypatch, capsys) -> None:
	"""cmd_auth_status tries localhost when host.docker.internal fails."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="valid-tok",
			cloud_endpoint="http://host.docker.internal:8000",
		),
	)

	response_data = json.dumps({"name": "local-team"}).encode()
	mock_resp = MagicMock()
	mock_resp.read.return_value = response_data
	mock_resp.__enter__ = lambda s: s
	mock_resp.__exit__ = MagicMock(return_value=False)
	requested_urls: list[str] = []

	def fake_urlopen(req, **_kw):
		"""Fail Docker-host endpoint and accept local host fallback."""
		requested_urls.append(req.full_url)
		if "host.docker.internal" in req.full_url:
			raise urllib.error.URLError("host unavailable")
		return mock_resp

	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	assert requested_urls == [
		"http://host.docker.internal:8000/api/v1/auth/me",
		"http://localhost:8000/api/v1/auth/me",
	]
	out = capsys.readouterr().out
	assert "local-team" in out


def test_cmd_auth_status_401_error(monkeypatch, capsys) -> None:
	"""cmd_auth_status with 401 reports token found but unverified."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="expired-tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	def raise_401(*args, **kwargs):
		"""Simulate HTTP 401 from cloud."""
		raise urllib.error.HTTPError(
			url="https://api.lerim.dev/api/v1/auth/me",
			code=401,
			msg="Unauthorized",
			hdrs={},
			fp=io.BytesIO(b""),
		)

	monkeypatch.setattr(urllib.request, "urlopen", raise_401)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "could not verify" in out


def test_cmd_auth_status_500_error(monkeypatch, capsys) -> None:
	"""cmd_auth_status with 500 reports token found but unverified."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="some-tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	def raise_500(*args, **kwargs):
		"""Simulate HTTP 500 from cloud."""
		raise urllib.error.HTTPError(
			url="https://api.lerim.dev/api/v1/auth/me",
			code=500,
			msg="Server Error",
			hdrs={},
			fp=io.BytesIO(b""),
		)

	monkeypatch.setattr(urllib.request, "urlopen", raise_500)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "could not verify" in out


def test_cmd_auth_status_network_error(monkeypatch, capsys) -> None:
	"""cmd_auth_status with network error reports token found but unverified."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="net-err-tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	def raise_url_error(*args, **kwargs):
		"""Simulate network failure."""
		raise urllib.error.URLError("connection refused")

	monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "could not verify" in out


def test_cmd_auth_status_json_error(monkeypatch, capsys) -> None:
	"""cmd_auth_status with invalid JSON response reports could not verify."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="json-err-tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	def raise_json_error(*args, **kwargs):
		"""Simulate invalid JSON response."""
		raise json.JSONDecodeError("test", "doc", 0)

	monkeypatch.setattr(urllib.request, "urlopen", raise_json_error)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "could not verify" in out


def test_cmd_auth_status_uses_name_fallback(monkeypatch, capsys) -> None:
	"""cmd_auth_status shows name when email is absent."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	response_data = json.dumps({"name": "Alice"}).encode()
	mock_resp = MagicMock()
	mock_resp.read.return_value = response_data
	mock_resp.__enter__ = lambda s: s
	mock_resp.__exit__ = MagicMock(return_value=False)

	monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: mock_resp)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "Alice" in out


def test_cmd_auth_status_unknown_fallback(monkeypatch, capsys) -> None:
	"""cmd_auth_status shows 'unknown' when response has no email or name."""
	monkeypatch.setattr(
		auth_mod, "get_config",
		lambda: MagicMock(
			cloud_token="tok",
			cloud_endpoint="https://api.lerim.dev",
		),
	)

	response_data = json.dumps({}).encode()
	mock_resp = MagicMock()
	mock_resp.read.return_value = response_data
	mock_resp.__enter__ = lambda s: s
	mock_resp.__exit__ = MagicMock(return_value=False)

	monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: mock_resp)

	args = argparse.Namespace()
	result = cmd_auth_status(args)

	assert result == 0
	out = capsys.readouterr().out
	assert "unknown" in out


# ---------------------------------------------------------------------------
# cmd_auth_logout
# ---------------------------------------------------------------------------


def test_cmd_auth_logout(monkeypatch, capsys) -> None:
	"""cmd_auth_logout clears token and returns 0."""
	saved: list[dict] = []
	monkeypatch.setattr(
		auth_mod, "save_config_patch", lambda patch: saved.append(patch)
	)

	args = argparse.Namespace()
	result = cmd_auth_logout(args)

	assert result == 0
	assert saved[0] == {"cloud": {"token": ""}}
	out = capsys.readouterr().out
	assert "Logged out" in out


# ---------------------------------------------------------------------------
# _run_browser_flow
# ---------------------------------------------------------------------------


def test_run_browser_flow_timeout(monkeypatch) -> None:
	"""_run_browser_flow returns None when no callback received."""
	class FakeHTTPServer:
		"""HTTPServer double that avoids binding a real socket."""

		def __init__(self, *args, **kwargs):
			"""Accept constructor arguments from the production flow."""
			self.timeout = None
			self.token_result = None

		def handle_request(self):
			"""Do not receive a callback token."""

		def server_close(self):
			"""Close the fake server."""

	class FakeThread:
		"""Thread double that keeps the unit test synchronous."""

		def __init__(self, target, daemon=False):
			"""Store the target without running it."""
			self.target = target
			self.daemon = daemon

		def start(self):
			"""Do not start background work."""

		def join(self, timeout=None):
			"""Return immediately."""

	monkeypatch.setattr(auth_mod, "_find_available_port", lambda: 9876)
	monkeypatch.setattr(auth_mod, "_TokenCallbackServer", FakeHTTPServer)
	monkeypatch.setattr(auth_mod.threading, "Thread", FakeThread)
	monkeypatch.setattr(auth_mod.webbrowser, "open", lambda url: None)
	result = _run_browser_flow("https://api.lerim.dev", timeout_seconds=1)
	assert result is None


def test_run_browser_flow_opens_correct_url(monkeypatch) -> None:
	"""_run_browser_flow URL-encodes the callback URL for browser auth."""
	class FakeHTTPServer:
		"""HTTPServer double that avoids binding a real socket."""

		def __init__(self, *args, **kwargs):
			"""Accept constructor arguments from the production flow."""
			self.timeout = None
			self.token_result = None

		def handle_request(self):
			"""Do not receive a callback token."""

		def server_close(self):
			"""Close the fake server."""

	class FakeThread:
		"""Thread double that keeps the unit test synchronous."""

		def __init__(self, target, daemon=False):
			"""Store the target without running it."""
			self.target = target
			self.daemon = daemon

		def start(self):
			"""Do not start background work."""

		def join(self, timeout=None):
			"""Return immediately."""

	monkeypatch.setattr(auth_mod, "_find_available_port", lambda: 9876)
	monkeypatch.setattr(auth_mod, "_TokenCallbackServer", FakeHTTPServer)
	monkeypatch.setattr(auth_mod.threading, "Thread", FakeThread)
	opened_urls: list[str] = []
	monkeypatch.setattr(
		auth_mod.webbrowser, "open", lambda url: opened_urls.append(url)
	)

	_run_browser_flow("https://api.lerim.dev", timeout_seconds=1)

	assert len(opened_urls) == 1
	parsed = urllib.parse.urlparse(opened_urls[0])
	params = urllib.parse.parse_qs(parsed.query)
	assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
		"https://api.lerim.dev/auth/cli"
	)
	assert params["callback"] == ["http://localhost:9876/callback"]


def test_run_browser_flow_success_path_isolated_between_runs(monkeypatch) -> None:
	"""Each browser-flow run should use only that server instance's token."""
	tokens = iter(["first-token", None])

	class FakeHTTPServer:
		"""HTTPServer double with per-instance token state."""

		def __init__(self, *args, **kwargs):
			"""Set token_result independently for each constructed server."""
			self.timeout = None
			self.token_result = next(tokens)

		def handle_request(self):
			"""No-op because token_result is preloaded per instance."""

		def server_close(self):
			"""Close the fake server."""

	class FakeThread:
		"""Thread double that keeps the unit test synchronous."""

		def __init__(self, target, daemon=False):
			"""Store the target without running it."""
			self.target = target
			self.daemon = daemon

		def start(self):
			"""Do not start background work."""

		def join(self, timeout=None):
			"""Return immediately."""

	monkeypatch.setattr(auth_mod, "_find_available_port", lambda: 9876)
	monkeypatch.setattr(auth_mod, "_TokenCallbackServer", FakeHTTPServer)
	monkeypatch.setattr(auth_mod.threading, "Thread", FakeThread)
	monkeypatch.setattr(auth_mod.webbrowser, "open", lambda url: None)

	assert _run_browser_flow("https://api.lerim.dev", timeout_seconds=1) == "first-token"
	assert _run_browser_flow("https://api.lerim.dev", timeout_seconds=1) is None
