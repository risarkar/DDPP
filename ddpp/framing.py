"""Incrementally frame newline-delimited TCP input."""

from .protocol import FORMAT_ERROR


class LineFramer:
    def __init__(self, max_bytes: int = 256):
        self.max_bytes = max_bytes
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data: bytes):
        """Yield complete line bodies or FORMAT_ERROR for oversized lines."""
        results = []
        for byte in data:
            if byte == 10:
                if self.discarding:
                    results.append(FORMAT_ERROR)
                else:
                    results.append(bytes(self.buffer))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                self.buffer.append(byte)
                if len(self.buffer) > self.max_bytes:
                    self.buffer.clear()
                    self.discarding = True
        return results
