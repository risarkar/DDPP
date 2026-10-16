"""DDPP coursework tests T-01 to T-19. Author: Rishiraj Sarkar.

T-20 is the physical LAN test documented in the report and evidence/lan.
Run from the project folder: python -B -m unittest discover -s tests -v
"""

from contextlib import ExitStack
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading
import time
import unittest

from ddpp.client import _receive_line
from ddpp.framing import LineFramer
from ddpp.server import DDPPServer

ROOT = Path(__file__).resolve().parents[1]
UNKNOWN = "0" * 32
EVIDENCE = Path(os.environ.get(
    "DDPP_TEST_LOG_DIR",
    str(ROOT / "evidence" / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")),
))


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for server state")


class Peer:
    def __init__(self, server):
        self.socket = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        self.reader = self.socket.makefile("rb")

    def send(self, command):
        data = command if isinstance(command, bytes) else (command + "\n").encode()
        print("  Request:", repr(data), flush=True)
        self.socket.sendall(data)

    def read(self):
        data = self.reader.readline(258)
        reply = data.decode().rstrip("\n") if data else "<EOF>"
        print("  Response:", reply, flush=True)
        return reply

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.reader.close()
        self.socket.close()


class DDPPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        cls.server = DDPPServer(port=0, log_file=EVIDENCE / "server_tests.log")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        wait_until(lambda: cls.server._listener is not None)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.thread.join(timeout=5)
        if cls.thread.is_alive():
            raise AssertionError("Server did not stop")

    def setUp(self):
        print("\n" + self._testMethodName, flush=True)

    def request(self, command):
        with Peer(self.server) as peer:
            peer.send(command)
            return peer.read()

    def store(self, reads=1, ttl=60, password="Demo123"):
        response = self.request(f"STORE {ttl} {reads} {password}")
        self.assertRegex(response, r"^200 OK [0-9a-f]{32}$")
        return response.split()[-1]

    def test_01_store_cross_client_and_info(self):
        token = self.store(reads=2)
        other = self.store()
        self.assertNotEqual(token, other)
        for _ in range(2):
            reply = self.request(f"INFO {token.upper()}")
            self.assertRegex(reply, r"^200 OK reads_remaining=2 expires_in=\d+$")
            self.assertLessEqual(int(reply.split("expires_in=")[1]), 60)
        for command in ("info", "retrieve", "quit"):
            args = [] if command == "quit" else [token]
            process = subprocess.run(
                [sys.executable, "-B", "-m", "ddpp.client", "--port",
                 str(self.server.port), command, *args],
                cwd=ROOT, capture_output=True, text=True, timeout=10,
            )
            print("  CLI", command, repr(process.stdout), repr(process.stderr))
            self.assertEqual(process.returncode, 0)
            self.assertTrue(process.stdout.startswith("200 OK"))
            self.assertNotIn("\x1b[", process.stdout)
        self.assertIn("reads_remaining=1", self.request(f"INFO {token}"))
        self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")

    def test_02_read_limits(self):
        for reads in (1, 10):
            token = self.store(reads=reads)
            for _ in range(reads):
                self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")
            self.assertEqual(self.request(f"RETRIEVE {token}"), "404 not found")
            self.assertEqual(self.request(f"INFO {token}"), "404 not found")

    def test_03_real_expiry(self):
        started = time.monotonic()
        unread = self.store()
        partial = self.store(reads=2)
        self.assertEqual(self.request(f"RETRIEVE {partial}"), "200 OK Demo123")
        with Peer(self.server) as first, Peer(self.server) as second:
            for peer, token in ((first, unread), (second, partial)):
                peer.socket.settimeout(70)
                peer.send(f"WATCH {token}")
                self.assertEqual(peer.read(), "200 OK watching")
            for peer, token in ((first, unread), (second, partial)):
                self.assertRegex(peer.read(), rf"^220 EXPIRED {token} \d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}Z$")
                self.assertEqual(peer.read(), "<EOF>")
        elapsed = time.monotonic() - started
        print(f"  Real expiration elapsed: {elapsed:.3f} seconds; sweep interval 1 second.")
        self.assertGreaterEqual(elapsed, 60)
        self.assertLess(elapsed, 70)
        for token in (unread, partial):
            for command in ("RETRIEVE", "INFO"):
                self.assertEqual(self.request(f"{command} {token}"), "404 not found")
        wait_until(lambda: unread not in self.server.store._entries)

    def test_04_password_validation(self):
        with Peer(self.server) as peer:
            for password in ("Pass!123", "Ab123", "A" * 65, "Abc123é"):
                peer.send(f"STORE 60 1 {password}")
                self.assertEqual(peer.read(), "303 invalid password")
            peer.send("QUIT")
            self.assertEqual(peer.read(), "200 OK bye")
        for password in ("Ab1234", "A" * 64):
            token = self.store(password=password)
            self.assertEqual(self.request(f"RETRIEVE {token}"), f"200 OK {password}")

    def test_05_field_counts(self):
        with Peer(self.server) as peer:
            for command in ("STORE 3600", "RETRIEVE", "INFO", "WATCH", "QUIT extra"):
                peer.send(command)
                self.assertEqual(peer.read(), "300 message format error")
            peer.send("QUIT")
            self.assertEqual(peer.read(), "200 OK bye")

    def test_06_generic_not_found(self):
        consumed = self.store()
        self.request(f"RETRIEVE {consumed}")
        for token in (UNKNOWN, consumed):
            for command in ("RETRIEVE", "INFO"):
                self.assertEqual(self.request(f"{command} {token}"), "404 not found")
        print("  Expired-token responses are also checked by T-03.")

    def test_07_partial_disconnect(self):
        wait_until(lambda: not self.server._threads)
        before = len(self.server.store._entries)
        with Peer(self.server) as peer:
            peer.send(b"STORE 60 1 Partial123")
        wait_until(lambda: not self.server._threads)
        self.assertEqual(len(self.server.store._entries), before)
        self.assertEqual(self.request("QUIT"), "200 OK bye")

    def test_08_unavailable_server(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            result = subprocess.run(
                [sys.executable, "-B", "-m", "ddpp.client", "--port",
                 str(reservation.getsockname()[1]), "quit"],
                cwd=ROOT, capture_output=True, text=True, timeout=10,
            )
        print("  Client exit:", result.returncode, "stderr:", result.stderr.strip())
        self.assertEqual(result.returncode, 1)
        self.assertIn("DDPP connection error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_09_combined_frames(self):
        with Peer(self.server) as peer:
            peer.send(f"INFO {UNKNOWN}\nQUIT\n".encode())
            self.assertEqual(peer.read(), "404 not found")
            self.assertEqual(peer.read(), "200 OK bye")
            self.assertEqual(peer.read(), "<EOF>")

    def test_10_split_frame(self):
        framer = LineFramer()
        self.assertEqual(framer.feed(b"STORE 60 1 Demo"), [])
        self.assertEqual(framer.feed(b"123\n"), [b"STORE 60 1 Demo123"])
        with Peer(self.server) as peer:
            peer.send(b"STORE 60 1 Demo")
            self.assertFalse(select.select([peer.socket], [], [], 0.05)[0])
            peer.send(b"123\n")
            response = peer.read()
            self.assertRegex(response, r"^200 OK [0-9a-f]{32}$")
            self.assertEqual(self.request("RETRIEVE " + response.split()[-1]), "200 OK Demo123")

    def test_11_simultaneous_retrieval(self):
        token = self.store()
        barrier = threading.Barrier(2)
        replies, errors = [], []

        def retrieve():
            try:
                with Peer(self.server) as peer:
                    barrier.wait(timeout=5)
                    peer.send(f"RETRIEVE {token}")
                    replies.append(peer.read())
            except Exception as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=retrieve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors, errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(replies, ["200 OK Demo123", "404 not found"])

    def test_12_multiple_watchers(self):
        token = self.store(reads=2)
        with ExitStack() as stack:
            watchers = [stack.enter_context(Peer(self.server)) for _ in range(3)]
            for peer in watchers:
                peer.send(f"WATCH {token}")
                self.assertEqual(peer.read(), "200 OK watching")
            self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")
            for peer in watchers:
                self.assertFalse(select.select([peer.socket], [], [], 0.05)[0])
            self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")
            for peer in watchers:
                self.assertRegex(peer.read(), rf"^210 BURNED {token} \d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}Z$")
                self.assertEqual(peer.read(), "<EOF>")

    def test_13_uniform_watch_reply(self):
        live = self.store()
        consumed = self.store()
        self.request(f"RETRIEVE {consumed}")
        # T-03 already checked real expiry. This small fixture sets up the
        # expired state again to compare acknowledgments without another wait.
        expired = self.store()
        with self.server.store._lock:
            self.server.store._entries[expired].expires_at = time.time() - 1
        for label, token in (("live", live), ("consumed", consumed),
                             ("expired fixture", expired), ("unknown", UNKNOWN)):
            print("  Token state:", label)
            self.assertEqual(self.request(f"WATCH {token}"), "200 OK watching")

    def test_14_ttl_and_read_boundaries(self):
        for ttl, reads in ((60, 1), (604800, 10)):
            token = self.store(ttl=ttl, reads=reads)
            self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")
        with Peer(self.server) as peer:
            for ttl in ("59", "604801", "-1", "1.5", "abc"):
                peer.send(f"STORE {ttl} 1 Demo123")
                self.assertEqual(peer.read(), "301 invalid TTL")
            for reads in ("0", "11", "-1", "1.5", "abc"):
                peer.send(f"STORE 60 {reads} Demo123")
                self.assertEqual(peer.read(), "302 invalid max-reads")
            peer.send("QUIT")
            self.assertEqual(peer.read(), "200 OK bye")

    def test_15_malformed_input_and_limits(self):
        framer = LineFramer()
        self.assertEqual(framer.feed(b"X" * 256 + b"\n"), [b"X" * 256])
        self.assertEqual(framer.feed(b"X" * 257 + b"\n"), ["300 message format error"])
        with Peer(self.server) as peer:
            for data in (b"HELLO\n", b"quit\n", b"\n", b"QUIT\t\n", b"QUIT\r\n",
                         b" QUIT\n", b"\xff\n", b"X" * 257 + b"\n",
                         b"RETRIEVE bad\n", b"INFO " + b"z" * 32 + b"\n"):
                peer.send(data)
                self.assertEqual(peer.read(), "300 message format error")
            peer.send("QUIT")
            self.assertEqual(peer.read(), "200 OK bye")
        for data in (b"200 OK\n", b"200 OK\r\n"):
            self.assertEqual(_receive_line(io.BytesIO(data)), "200 OK")
        for data in (b"", b"200 OK", b"X" * 257 + b"\n", b"\xff\n"):
            with self.assertRaises((ConnectionError, UnicodeError)):
                _receive_line(io.BytesIO(data))

    def test_16_watch_recovery_quit_and_cleanup(self):
        for tail in (b"QUIT\n", b"QU"):
            with Peer(self.server) as peer:
                peer.send(f"WATCH {UNKNOWN}\n".encode() + tail)
                self.assertEqual(peer.read(), "200 OK watching")
                if tail == b"QU":
                    peer.send(b"IT\n")
                self.assertEqual(peer.read(), "200 OK bye")
                self.assertEqual(peer.read(), "<EOF>")
        token = self.store()
        with Peer(self.server) as peer:
            peer.send(f"WATCH {token}")
            self.assertEqual(peer.read(), "200 OK watching")
            peer.send("INVALID")
            self.assertEqual(peer.read(), "300 message format error")
            peer.send("QUIT")
            self.assertEqual(peer.read(), "200 OK bye")
            self.assertEqual(peer.read(), "<EOF>")
        wait_until(lambda: not self.server.store._entries[token].watchers)
        with Peer(self.server) as peer:
            peer.send(f"WATCH {token}")
            self.assertEqual(peer.read(), "200 OK watching")
        wait_until(lambda: not self.server.store._entries[token].watchers)
        self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")

    def test_17_quit_shutdown_and_restart(self):
        self.assertEqual(self.request("QUIT"), "200 OK bye")
        old_token = None
        for iteration in range(2):
            server = DDPPServer(port=0, log_file=EVIDENCE / "shutdown_restart.log")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                wait_until(lambda: server._listener is not None)
                with Peer(server) as ordinary, Peer(server) as watcher:
                    if iteration == 0:
                        ordinary.send("STORE 60 1 Restart123")
                        old_token = ordinary.read().split()[-1]
                    else:
                        ordinary.send(f"RETRIEVE {old_token}")
                        self.assertEqual(ordinary.read(), "404 not found")
                    watcher.send(f"WATCH {UNKNOWN}")
                    self.assertEqual(watcher.read(), "200 OK watching")
                    server.stop()
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    self.assertFalse(server._threads)
                    self.assertEqual(ordinary.read(), "<EOF>")
                    self.assertEqual(watcher.read(), "<EOF>")
            finally:
                server.stop()
                thread.join(timeout=5)
        text = (EVIDENCE / "shutdown_restart.log").read_text(encoding="utf-8")
        self.assertEqual(text.count("event=server_stopped"), 2)

    def test_18_log_privacy(self):
        token = self.store(password="LogCheck123")
        self.request(f"RETRIEVE {token}")
        wait_until(lambda: not self.server._threads)
        text = (EVIDENCE / "server_tests.log").read_text(encoding="utf-8")
        for value in ("LogCheck123", "Demo123", "Partial123", "Pass!123", token):
            self.assertNotIn(value, text)
        self.assertNotRegex(text, r"\b[0-9a-fA-F]{32}\b")
        for command in ("STORE", "RETRIEVE", "INFO", "WATCH", "QUIT", "MALFORMED"):
            self.assertIn(f"command={command}", text)
        for code in (200, 300, 301, 302, 303, 404):
            self.assertIn(f"result={code}", text)
        for notice in ("BURNED", "EXPIRED"):
            self.assertIn(f"notice={notice}", text)
        print("  Logs contain commands, codes and notices without passwords or full tokens.")

    def test_19_one_and_ten_client_latency(self):
        for count in (1, 10):
            barrier = threading.Barrier(count)
            timings, errors = [], []

            def work(index):
                try:
                    with Peer(self.server) as peer:
                        barrier.wait(timeout=5)
                        password = f"Load{index:03d}"
                        started = time.perf_counter()
                        peer.socket.sendall(f"STORE 60 1 {password}\n".encode())
                        stored = peer.reader.readline().decode().strip()
                        store_ms = (time.perf_counter() - started) * 1000
                        self.assertRegex(stored, r"^200 OK [0-9a-f]{32}$")
                        started = time.perf_counter()
                        peer.socket.sendall(f"RETRIEVE {stored.split()[-1]}\n".encode())
                        retrieved = peer.reader.readline().decode().strip()
                        retrieve_ms = (time.perf_counter() - started) * 1000
                        self.assertEqual(retrieved, f"200 OK {password}")
                        timings.append((store_ms, retrieve_ms))
                except Exception as exc:
                    errors.append(repr(exc))

            workers = [threading.Thread(target=work, args=(i,)) for i in range(count)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
            self.assertFalse(errors, errors)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(len(timings), count)
            max_store = max(t[0] for t in timings)
            max_retrieve = max(t[1] for t in timings)
            print(f"  {count} clients: {count}/{count} STORE and RETRIEVE passed. "
                  f"Maximum STORE {max_store:.3f} ms, RETRIEVE {max_retrieve:.3f} ms.")
            self.assertLess(max_store, 200)
            self.assertLess(max_retrieve, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
