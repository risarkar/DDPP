import socket
import threading
import time
import unittest

from ddpp.server import DDPPServer


class PerformanceTests(unittest.TestCase):
    def test_ten_simultaneous_store_requests_finish_under_200_ms_each(self):
        server = DDPPServer(port=0)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        deadline = time.monotonic() + 3
        while server._listener is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(server._listener)

        barrier = threading.Barrier(11)
        durations = []
        responses = []
        errors = []

        def store(index):
            try:
                with socket.create_connection(("127.0.0.1", server.port), timeout=2) as connection:
                    connection.settimeout(2)
                    barrier.wait()
                    started = time.perf_counter()
                    connection.sendall(f"STORE 60 1 Pass{index:03d}\n".encode("ascii"))
                    response = bytearray()
                    while not response.endswith(b"\n"):
                        response.extend(connection.recv(256))
                    durations.append(time.perf_counter() - started)
                    responses.append(response.decode("ascii").strip())
            except Exception as exc:  # Preserve worker failures for the test thread.
                errors.append(exc)

        workers = [threading.Thread(target=store, args=(index,)) for index in range(10)]
        try:
            for worker in workers:
                worker.start()
            barrier.wait(timeout=3)
            for worker in workers:
                worker.join(timeout=3)
            self.assertFalse(errors)
            self.assertEqual(len(responses), 10)
            self.assertTrue(all(response.startswith("200 OK ") for response in responses))
            self.assertLess(max(durations), 0.200)
        finally:
            server.stop()
            server_thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
