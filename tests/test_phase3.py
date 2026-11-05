"""Six focused Phase 3 scenarios. Author: Rishiraj Sarkar.

Uses the same plain unittest/socket approach as the submitted Phase 2 suite.
Run both suites: python -B -m unittest discover -s tests -v
"""

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from ddpp.client import interactive_session
from ddpp.server import DDPPServer
from test_ddpp import EVIDENCE, Peer, UNKNOWN, wait_until

ROOT = Path(__file__).resolve().parents[1]


class Phase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        cls.server = DDPPServer(port=0, log_file=EVIDENCE / "phase3_server.log")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        wait_until(lambda: cls.server._listener is not None)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.thread.join(timeout=5)
        if cls.thread.is_alive():
            raise AssertionError("Phase 3 server did not stop")

    def setUp(self):
        print("\n" + self._testMethodName, flush=True)

    def request(self, command):
        with Peer(self.server) as peer:
            peer.send(command)
            return peer.read()

    def store(self, reads=1):
        reply = self.request(f"STORE 60 {reads} Demo123")
        self.assertRegex(reply, r"^200 OK [0-9a-f]{32}$")
        return reply.split()[-1]

    def cli(self, *arguments):
        return [sys.executable, "-B", "-m", "ddpp.client", "--port",
                str(self.server.port), *arguments]

    def test_p3_01_persistent_client_session(self):
        token = self.store()
        before = self.server._connection_counter
        process = subprocess.run(
            self.cli("session"), input=f"help\ninfo {token}\nretrieve {token}\ninvalid\nquit\n",
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        print(process.stdout, process.stderr)
        self.assertEqual(process.returncode, 0)
        self.assertIn("reads_remaining=1", process.stdout)
        self.assertIn("200 OK Demo123", process.stdout)
        self.assertIn("Input error: 300 message format error", process.stdout)
        self.assertIn("200 OK bye", process.stdout)
        self.assertIn(f"--host 127.0.0.1 --port {self.server.port} watch TOKEN", process.stdout)
        self.assertEqual(self.server._connection_counter - before, 1)
        # Test the hidden-password prompt function with supplied input, using a real socket.
        output = io.StringIO()
        with patch("builtins.input", side_effect=["store 60 1", "quit"]), \
                patch("getpass.getpass", return_value="Prompt123"), redirect_stdout(output):
            interactive_session("127.0.0.1", self.server.port)
        transcript = output.getvalue()
        print("Prompt-function transcript:", transcript)
        stored = next(line.split()[-1] for line in transcript.splitlines()
                      if line.startswith("200 OK ") and len(line.split()[-1]) == 32)
        self.assertEqual(self.request(f"RETRIEVE {stored}"), "200 OK Prompt123")

    def test_p3_02_watch_wait_limit_and_notice(self):
        process = subprocess.run(self.cli("watch", UNKNOWN, "--timeout", "0.25"),
                                 cwd=ROOT, capture_output=True, text=True, timeout=10)
        print("Limited WATCH:", process.stdout, process.stderr)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout.strip(), "200 OK watching")
        self.assertIn("does not determine token validity", process.stderr)
        self.assertIn("Ctrl+C", process.stderr)
        token = self.store()
        watcher = subprocess.Popen(self.cli("watch", token, "--timeout", "5"),
                                   cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            wait_until(lambda: bool(self.server.store._entries[token].watchers))
            self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")
            stdout, stderr = watcher.communicate(timeout=10)
            print("Notified WATCH:", stdout, stderr)
            self.assertEqual(watcher.returncode, 0)
            self.assertIn("200 OK watching\n210 BURNED " + token, stdout)
            self.assertNotIn("Waiting limit reached", stderr)
        finally:
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate()
        for value in ("0", "nan"):
            rejected = subprocess.run(self.cli("watch", UNKNOWN, "--timeout", value),
                                      cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(rejected.returncode, 2)

    def test_p3_03_three_clients_mixed_operations(self):
        token = self.store(reads=2)
        barrier = threading.Barrier(3)
        replies, errors = {}, []
        commands = ["STORE 60 1 Mixed123", f"INFO {token}", f"RETRIEVE {token}"]
        with ExitStack() as stack:
            peers = [stack.enter_context(Peer(self.server)) for _ in range(3)]
            def work(index):
                try:
                    barrier.wait(timeout=5)
                    peers[index].send(commands[index])
                    replies[index] = peers[index].read()
                except Exception as exc:
                    errors.append(repr(exc))
            workers = [threading.Thread(target=work, args=(i,)) for i in range(3)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
            self.assertFalse(errors, errors)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertRegex(replies[0], r"^200 OK [0-9a-f]{32}$")
            self.assertRegex(replies[1], r"^200 OK reads_remaining=[12] expires_in=\d+$")
            self.assertEqual(replies[2], "200 OK Demo123")
            peers[1].send("RETRIEVE " + replies[0].split()[-1])
            self.assertEqual(peers[1].read(), "200 OK Mixed123")
            peers[0].send(f"INFO {token}")
            self.assertIn("reads_remaining=1", peers[0].read())
        print("Three held connections completed different operations against shared state.")

    def test_p3_04_failure_isolation_and_churn(self):
        token = self.store(reads=2)
        with Peer(self.server) as first, Peer(self.server) as second:
            with Peer(self.server) as offender:
                offender.send("STORE bad 1 Demo123")
                self.assertEqual(offender.read(), "301 invalid TTL")
                first.send(f"INFO {token}")
                self.assertIn("reads_remaining=2", first.read())
                second.send(f"RETRIEVE {token}")
                self.assertEqual(second.read(), "200 OK Demo123")
                offender.send(b"STORE 60 1 Incomplete")
                # Close without QUIT or a newline. Both healthy connections remain open.
            first.send(f"INFO {token}")
            self.assertIn("reads_remaining=1", first.read())
            second.send(f"RETRIEVE {token}")
            self.assertEqual(second.read(), "200 OK Demo123")
            for _ in range(20):
                self.assertEqual(self.request("QUIT"), "200 OK bye")
            first.send(f"INFO {token}")
            self.assertEqual(first.read(), "404 not found")
            second.send("QUIT")
            self.assertEqual(second.read(), "200 OK bye")
        wait_until(lambda: not self.server._threads)
        print("Healthy sessions survived invalid input, abrupt EOF and 20 connection cycles.")

    def test_p3_05_burst_partial_and_watch_state(self):
        with Peer(self.server) as burst:
            burst.send(((f"INFO {UNKNOWN}\n" * 20) + "QUIT\n").encode())
            for _ in range(20):
                self.assertEqual(burst.read(), "404 not found")
            self.assertEqual(burst.read(), "200 OK bye")
            self.assertEqual(burst.read(), "<EOF>")
        with Peer(self.server) as partial:
            partial.send(b"IN")
            self.assertEqual(self.request("QUIT"), "200 OK bye")
            partial.send(f"FO {UNKNOWN}\nQUIT\n".encode())
            self.assertEqual(partial.read(), "404 not found")
            self.assertEqual(partial.read(), "200 OK bye")
        token = self.store()
        with Peer(self.server) as watcher:
            watcher.send(f"WATCH {token}")
            self.assertEqual(watcher.read(), "200 OK watching")
            watcher.send(f"INFO {token}")
            self.assertEqual(watcher.read(), "300 message format error")
            self.assertEqual(self.request(f"RETRIEVE {token}"), "200 OK Demo123")
            self.assertTrue(watcher.read().startswith(f"210 BURNED {token} "))
            self.assertEqual(watcher.read(), "<EOF>")

    def test_p3_06_ten_client_load(self):
        clients, pairs = 10, 10
        barrier = threading.Barrier(clients)
        timings, failures = [], []
        started = time.perf_counter()
        def work(index):
            try:
                with socket.create_connection(("127.0.0.1", self.server.port), timeout=5) as peer:
                    with peer.makefile("rb") as reader:
                        barrier.wait(timeout=5)
                        for number in range(pairs):
                            password = f"Load{index:02d}{number:02d}"
                            tick = time.perf_counter()
                            peer.sendall(f"STORE 60 1 {password}\n".encode())
                            reply = reader.readline(258).decode().strip()
                            elapsed = (time.perf_counter() - tick) * 1000
                            self.assertRegex(reply, r"^200 OK [0-9a-f]{32}$")
                            timings.append(elapsed)
                            tick = time.perf_counter()
                            peer.sendall(f"RETRIEVE {reply.split()[-1]}\n".encode())
                            reply = reader.readline(258).decode().strip()
                            elapsed = (time.perf_counter() - tick) * 1000
                            self.assertEqual(reply, f"200 OK {password}")
                            timings.append(elapsed)
            except Exception as exc:
                failures.append(repr(exc))
        workers = [threading.Thread(target=work, args=(i,)) for i in range(clients)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        duration = time.perf_counter() - started
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertFalse(failures, failures)
        self.assertEqual(len(timings), clients * pairs * 2)
        metrics = {
            "clients": clients, "requests": clients * pairs * 2,
            "successful_requests": len(timings), "failed_requests": 0,
            "duration_seconds": duration, "mean_response_ms": statistics.mean(timings),
            "maximum_response_ms": max(timings),
            "setup": "IPv4 loopback, persistent connections, audit logging enabled",
            "duration_scope": "Thread startup, connection setup, all requests and worker joins",
            "latency_scope": "Per-request send through complete response, excluding connection setup",
        }
        (EVIDENCE / "load_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        print("Load result:", json.dumps(metrics, sort_keys=True), flush=True)
        self.assertLess(max(timings), 200, "Phase 1's 200 ms local target was exceeded")


if __name__ == "__main__":
    unittest.main()
