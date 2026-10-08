import hashlib
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from ddpp.server import DDPPServer, discover_lan_addresses, token_tag


class RunningServer:
    def __init__(self, **kwargs):
        self.server = DDPPServer(port=0, sweep_interval=0.05, **kwargs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 3
        while self.server._listener is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self.server._listener is None:
            raise RuntimeError("test server did not start")
        return self.server

    def __exit__(self, exc_type, exc, traceback):
        self.server.stop()
        self.thread.join(timeout=3)


def request(host, port, command):
    with socket.create_connection((host, port), timeout=2) as connection:
        connection.settimeout(2)
        connection.sendall((command + "\n").encode("ascii"))
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = connection.recv(256)
            if not chunk:
                break
            data.extend(chunk)
        return data.decode("utf-8").strip()


class LanAndLoggingTests(unittest.TestCase):
    def test_lan_mode_binds_all_ipv4_interfaces(self):
        with RunningServer(host="0.0.0.0") as server:
            bound_host, bound_port = server._listener.getsockname()[:2]
            self.assertEqual(bound_host, "0.0.0.0")
            self.assertEqual(request("127.0.0.1", bound_port, "QUIT"), "200 OK bye")

            # When this machine exposes a LAN address, verify that interface too.
            addresses = discover_lan_addresses()
            if addresses:
                self.assertEqual(request(addresses[0], bound_port, "QUIT"), "200 OK bye")

    def test_audit_log_records_interactions_without_secrets(self):
        # Keep the temporary log under the repository for restricted sandboxes.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            log_path = Path(directory) / "server.log"
            with RunningServer(host="127.0.0.1", log_file=log_path) as server:
                stored = request("127.0.0.1", server.port, "STORE 60 1 Pass123")
                token = stored.split()[-1]
                self.assertEqual(
                    request("127.0.0.1", server.port, f"INFO {token}").split()[0:2],
                    ["200", "OK"],
                )
                self.assertEqual(
                    request("127.0.0.1", server.port, f"RETRIEVE {token}"),
                    "200 OK Pass123",
                )
                self.assertEqual(
                    request("127.0.0.1", server.port, f"RETRIEVE {token}"),
                    "404 not found",
                )

            audit = log_path.read_text(encoding="utf-8")
            self.assertIn("event=server_started", audit)
            self.assertIn("command=STORE result=200", audit)
            self.assertIn("command=INFO result=200", audit)
            self.assertIn("command=RETRIEVE result=200", audit)
            self.assertIn("command=RETRIEVE result=404", audit)
            self.assertIn("event=server_stopped", audit)
            self.assertIn(f"token_tag={token_tag(token)}", audit)
            self.assertNotIn("Pass123", audit)
            self.assertNotIn(token, audit)

    def test_token_tag_is_deterministic_and_does_not_reveal_token(self):
        token = "a" * 32
        expected = hashlib.sha256(token.encode("ascii")).hexdigest()[:12]
        self.assertEqual(token_tag(token), expected)
        self.assertNotIn(token, token_tag(token))


if __name__ == "__main__":
    unittest.main()
