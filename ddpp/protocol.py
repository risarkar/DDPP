"""Parse and validate one complete DDPP command line."""

from dataclasses import dataclass
import re

FORMAT_ERROR = "300 message format error"
INVALID_TTL = "301 invalid TTL"
INVALID_READS = "302 invalid max-reads"
INVALID_PASSWORD = "303 invalid password"
NOT_FOUND = "404 not found"

_PASSWORD = re.compile(r"[A-Za-z0-9]{6,64}\Z", re.ASCII)
_TOKEN = re.compile(r"[0-9a-fA-F]{32}\Z", re.ASCII)
_INTEGER = re.compile(r"[0-9]+\Z", re.ASCII)


@dataclass(frozen=True)
class Command:
    name: str
    args: tuple


class ProtocolError(Exception):
    def __init__(self, response: str):
        super().__init__(response)
        self.response = response


def parse(line: bytes) -> Command:
    if len(line) > 256:
        raise ProtocolError(FORMAT_ERROR)
    try:
        value = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(FORMAT_ERROR) from exc
    if not value or value.strip() != value or "\r" in value or "\t" in value:
        raise ProtocolError(FORMAT_ERROR)
    fields = value.split(" ")
    if any(not field for field in fields):
        raise ProtocolError(FORMAT_ERROR)
    name = fields[0]
    expected = {"STORE": 4, "RETRIEVE": 2, "INFO": 2, "WATCH": 2, "QUIT": 1}
    if name not in expected or len(fields) != expected[name]:
        raise ProtocolError(FORMAT_ERROR)
    if name == "STORE":
        ttl, reads, password = fields[1:]
        if not _INTEGER.fullmatch(ttl) or not 60 <= int(ttl) <= 604800:
            raise ProtocolError(INVALID_TTL)
        if not _INTEGER.fullmatch(reads) or not 1 <= int(reads) <= 10:
            raise ProtocolError(INVALID_READS)
        if not _PASSWORD.fullmatch(password):
            raise ProtocolError(INVALID_PASSWORD)
        return Command(name, (int(ttl), int(reads), password))
    if name != "QUIT":
        if not _TOKEN.fullmatch(fields[1]):
            raise ProtocolError(FORMAT_ERROR)
        return Command(name, (fields[1].lower(),))
    return Command(name, ())


def line(response: str) -> bytes:
    return (response + "\n").encode("utf-8")
