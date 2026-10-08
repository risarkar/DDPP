import unittest

from ddpp.framing import LineFramer
from ddpp.protocol import Command, ProtocolError, parse


class ProtocolTests(unittest.TestCase):
    def test_commands_and_validation(self):
        self.assertEqual(parse(b"STORE 60 1 Pass123"), Command("STORE", (60, 1, "Pass123")))
        self.assertEqual(parse(b"QUIT"), Command("QUIT", ()))
        token = b"a" * 32
        self.assertEqual(parse(b"RETRIEVE " + token).args, ("a" * 32,))
        cases = {
            b"STORE 3600": "300 message format error",
            b"STORE nope 1 Pass123": "301 invalid TTL",
            b"STORE 59 1 Pass123": "301 invalid TTL",
            b"STORE 60 11 Pass123": "302 invalid max-reads",
            b"STORE 60 1 Pass!123": "303 invalid password",
            b"RETRIEVE bad": "300 message format error",
            b"WHAT": "300 message format error",
            b"QUIT extra": "300 message format error",
            b"\xff": "300 message format error",
        }
        for command, response in cases.items():
            with self.subTest(command=command), self.assertRaises(ProtocolError) as caught:
                parse(command)
            self.assertEqual(caught.exception.response, response)

    def test_framing_split_coalesced_and_overlong(self):
        framer = LineFramer()
        self.assertEqual(framer.feed(b"QU"), [])
        self.assertEqual(framer.feed(b"IT\nQUIT\n"), [b"QUIT", b"QUIT"])
        self.assertEqual(framer.feed(b"x" * 257 + b"\nQUIT\n"),
                         ["300 message format error", b"QUIT"])
        self.assertEqual(framer.feed(b"PARTIAL"), [])


if __name__ == "__main__":
    unittest.main()
