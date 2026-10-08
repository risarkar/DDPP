import io
import unittest

from ddpp.client import _receive_line


class ClientTests(unittest.TestCase):
    def test_receive_line_accepts_lf_and_crlf(self):
        self.assertEqual(_receive_line(io.BytesIO(b"200 OK\n")), "200 OK")
        self.assertEqual(_receive_line(io.BytesIO(b"200 OK\r\n")), "200 OK")

    def test_receive_line_rejects_closed_incomplete_and_oversized(self):
        for message in (b"", b"200 OK", b"x" * 257 + b"\n"):
            with self.subTest(message_length=len(message)), self.assertRaises(ConnectionError):
                _receive_line(io.BytesIO(message))

    def test_receive_line_rejects_invalid_utf8(self):
        with self.assertRaises(UnicodeDecodeError):
            _receive_line(io.BytesIO(b"\xff\n"))


if __name__ == "__main__":
    unittest.main()
