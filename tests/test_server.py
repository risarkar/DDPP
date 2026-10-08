import socket
import threading
import time
import unittest

from ddpp.server import DDPPServer


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DDPPServer(port=0, sweep_interval=0.05)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 3
        while cls.server._listener is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if cls.server._listener is None:
            raise RuntimeError("test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.thread.join(timeout=3)

    def connect(self):
        connection = socket.create_connection(("127.0.0.1", self.server.port), timeout=2)
        connection.settimeout(2)
        return connection

    def read(self, connection):
        data = bytearray()
        while not data.endswith(b"\n"):
            data.extend(connection.recv(1))
        return data.decode().strip()

    def store(self, reads=1):
        with self.connect() as connection:
            connection.sendall(f"STORE 60 {reads} Pass123\n".encode())
            response = self.read(connection)
        self.assertTrue(response.startswith("200 OK "))
        return response.split()[-1]

    def test_end_to_end_and_framing(self):
        token = self.store()
        with self.connect() as connection:
            connection.sendall(f"INFO {token}\nRETRIEVE {token}\n".encode())
            self.assertTrue(self.read(connection).startswith("200 OK reads_remaining=1"))
            self.assertEqual(self.read(connection), "200 OK Pass123")
            connection.sendall(f"RETRIEVE {token[:10]}".encode())
            connection.sendall(f"{token[10:]}\n".encode())
            self.assertEqual(self.read(connection), "404 not found")
            connection.sendall(b"QUIT\n")
            self.assertEqual(self.read(connection), "200 OK bye")
            self.assertEqual(connection.recv(1), b"")

    def test_errors_recover(self):
        with self.connect() as connection:
            connection.sendall(b"STORE 60 1 Pass!123\nSTORE 3600\n")
            self.assertEqual(self.read(connection), "303 invalid password")
            self.assertEqual(self.read(connection), "300 message format error")
            connection.sendall(b"X" * 257 + b"\nQUIT\n")
            self.assertEqual(self.read(connection), "300 message format error")
            self.assertEqual(self.read(connection), "200 OK bye")

    def test_watch_notice_and_uniform_ack(self):
        token = self.store()
        valid = self.connect()
        second_valid = self.connect()
        third_valid = self.connect()
        unknown = self.connect()
        try:
            valid.sendall(f"WATCH {token}\n".encode())
            second_valid.sendall(f"WATCH {token}\n".encode())
            third_valid.sendall(f"WATCH {token}\n".encode())
            unknown.sendall(f"WATCH {'f' * 32}\n".encode())
            self.assertEqual(self.read(valid), "200 OK watching")
            self.assertEqual(self.read(second_valid), "200 OK watching")
            self.assertEqual(self.read(third_valid), "200 OK watching")
            self.assertEqual(self.read(unknown), "200 OK watching")
            with self.connect() as retriever:
                retriever.sendall(f"RETRIEVE {token}\n".encode())
                self.assertEqual(self.read(retriever), "200 OK Pass123")
            self.assertTrue(self.read(valid).startswith(f"210 BURNED {token} "))
            self.assertTrue(self.read(second_valid).startswith(f"210 BURNED {token} "))
            self.assertTrue(self.read(third_valid).startswith(f"210 BURNED {token} "))
        finally:
            valid.close()
            second_valid.close()
            third_valid.close()
            unknown.close()

    def test_two_clients_race(self):
        token = self.store()
        barrier = threading.Barrier(3)
        results = []

        def retrieve():
            with self.connect() as connection:
                barrier.wait()
                connection.sendall(f"RETRIEVE {token}\n".encode())
                results.append(self.read(connection))

        threads = [threading.Thread(target=retrieve) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=3)
        self.assertCountEqual(results, ["200 OK Pass123", "404 not found"])

    def test_partial_disconnect(self):
        with self.connect() as connection:
            connection.sendall(b"STORE 60 1 Pass")
        with self.connect() as connection:
            connection.sendall(b"QUIT\n")
            self.assertEqual(self.read(connection), "200 OK bye")


if __name__ == "__main__":
    unittest.main()
