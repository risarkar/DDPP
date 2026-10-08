"""Concurrent DDPP TCP server."""

import argparse
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import threading
import time

from .framing import LineFramer
from .protocol import FORMAT_ERROR, NOT_FOUND, ProtocolError, line, parse
from .store import PasswordStore, Watcher


class UTCFormatter(logging.Formatter):
    """Format log timestamps in UTC so evidence is unambiguous."""

    converter = time.gmtime


def build_audit_logger(log_file, max_bytes=1_000_000, backup_count=3):
    """Create a private rotating logger for one server instance."""
    logger = logging.getLogger(f"ddpp.audit.{time.time_ns()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if log_file is None:
        logger.addHandler(logging.NullHandler())
        return logger

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(UTCFormatter("%(asctime)sZ level=%(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)
    return logger


def discover_lan_addresses():
    """Return non-loopback IPv4 addresses that LAN clients can try."""
    addresses = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if not address.startswith("127.") and address != "0.0.0.0":
                addresses.add(address)
    except OSError:
        pass

    # A UDP connect selects a local interface without sending application data.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
            if not address.startswith("127.") and address != "0.0.0.0":
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def token_tag(token):
    """Create a non-secret correlation tag without logging the token."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()[:12]


class DDPPServer:
    def __init__(
        self,
        host="127.0.0.1",
        port=5270,
        *,
        sweep_interval=1.0,
        max_clients=64,
        log_file=None,
        log_max_bytes=1_000_000,
        log_backup_count=3,
    ):
        self.host = host
        self.port = port
        self.sweep_interval = sweep_interval
        self.store = PasswordStore()
        self._slots = threading.BoundedSemaphore(max_clients)
        self._stop = threading.Event()
        self._listener = None
        self._threads = set()
        self._thread_lock = threading.Lock()
        self._connection_counter = 0
        self._logger = build_audit_logger(log_file, log_max_bytes, log_backup_count)

    def serve_forever(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen()
            listener.settimeout(0.2)
            self._listener = listener
            self.port = listener.getsockname()[1]
            print(f"DDPP listening on {self.host}:{self.port}", flush=True)
            if self.host == "0.0.0.0":
                addresses = discover_lan_addresses()
                for address in addresses:
                    print(
                        f"LAN clients: python -m ddpp.client --host {address} --port {self.port} info TOKEN",
                        flush=True,
                    )
                if not addresses:
                    print("LAN mode enabled; use this server's local IPv4 address.", flush=True)
                print("The host firewall must allow this TCP port on private networks.", flush=True)
            self._logger.info("event=server_started bind=%s port=%d", self.host, self.port)
            sweeper = threading.Thread(target=self._sweep_loop, daemon=True)
            sweeper.start()
            try:
                while not self._stop.is_set():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        if self._stop.is_set():
                            break
                        raise
                    if not self._slots.acquire(blocking=False):
                        self._logger.warning("event=connection_rejected peer=%s reason=client_limit", self._peer(connection))
                        connection.close()
                        continue
                    self._connection_counter += 1
                    connection_id = self._connection_counter
                    peer = self._peer(connection)
                    thread = threading.Thread(
                        target=self._handle_client,
                        args=(connection, connection_id, peer),
                        daemon=True,
                    )
                    with self._thread_lock:
                        self._threads.add(thread)
                    thread.start()
            finally:
                self._stop.set()
                sweeper.join(timeout=2)
                self._listener = None
                with self._thread_lock:
                    client_threads = list(self._threads)
                for thread in client_threads:
                    thread.join(timeout=1)
                self._logger.info("event=server_stopped")
                for handler in self._logger.handlers:
                    handler.flush()
                    handler.close()

    def stop(self):
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

    def _sweep_loop(self):
        while not self._stop.wait(self.sweep_interval):
            expired = self.store.sweep()
            if expired:
                self._logger.info("event=expiration_sweep expired_count=%d", expired)

    def _send(self, connection, response):
        connection.sendall(line(response))

    @staticmethod
    def _peer(connection):
        try:
            host, port = connection.getpeername()[:2]
            return f"{host}:{port}"
        except OSError:
            return "unknown"

    def _log_command(self, connection_id, peer, command, result, **fields):
        suffix = "".join(f" {name}={value}" for name, value in fields.items())
        self._logger.info(
            "event=command connection_id=%d peer=%s command=%s result=%s%s",
            connection_id,
            peer,
            command,
            result,
            suffix,
        )

    def _handle_client(self, connection, connection_id, peer):
        framer = LineFramer()
        reason = "eof"
        self._logger.info("event=connection_opened connection_id=%d peer=%s", connection_id, peer)
        try:
            with connection:
                connection.settimeout(0.5)
                while not self._stop.is_set():
                    try:
                        data = connection.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    for item in framer.feed(data):
                        if item == FORMAT_ERROR:
                            self._send(connection, FORMAT_ERROR)
                            self._log_command(connection_id, peer, "MALFORMED", 300)
                            continue
                        try:
                            command = parse(item)
                        except ProtocolError as exc:
                            self._send(connection, exc.response)
                            self._log_command(connection_id, peer, "MALFORMED", exc.response.split()[0])
                            continue
                        name = command.name
                        if name == "QUIT":
                            self._send(connection, "200 OK bye")
                            self._log_command(connection_id, peer, name, 200)
                            reason = "quit"
                            return
                        if name == "STORE":
                            token = self.store.store(*command.args)
                            self._send(connection, f"200 OK {token}")
                            self._log_command(
                                connection_id,
                                peer,
                                name,
                                200,
                                ttl=command.args[0],
                                max_reads=command.args[1],
                                token_tag=token_tag(token),
                            )
                        elif name == "RETRIEVE":
                            password = self.store.retrieve(command.args[0])
                            self._send(connection, f"200 OK {password}" if password is not None else NOT_FOUND)
                            self._log_command(
                                connection_id,
                                peer,
                                name,
                                200 if password is not None else 404,
                                token_tag=token_tag(command.args[0]),
                            )
                        elif name == "INFO":
                            result = self.store.info(command.args[0])
                            if result is None:
                                self._send(connection, NOT_FOUND)
                            else:
                                reads, seconds = result
                                self._send(connection, f"200 OK reads_remaining={reads} expires_in={seconds}")
                            self._log_command(
                                connection_id,
                                peer,
                                name,
                                200 if result is not None else 404,
                                token_tag=token_tag(command.args[0]),
                            )
                        elif name == "WATCH":
                            self._log_command(
                                connection_id,
                                peer,
                                name,
                                200,
                                token_tag=token_tag(command.args[0]),
                            )
                            reason = self._watch(connection, command.args[0], connection_id, peer)
                            return
        except OSError as exc:
            reason = f"socket_error:{type(exc).__name__}"
        finally:
            self._logger.info(
                "event=connection_closed connection_id=%d peer=%s reason=%s",
                connection_id,
                peer,
                reason,
            )
            self._slots.release()
            with self._thread_lock:
                self._threads.discard(threading.current_thread())

    def _watch(self, connection, token, connection_id, peer):
        watcher = Watcher()
        self.store.watch(token, watcher)
        try:
            self._send(connection, "200 OK watching")
            deadline = time.monotonic() + 604800
            connection.settimeout(0.2)
            while not self._stop.is_set() and time.monotonic() < deadline:
                if watcher.event.is_set():
                    self._send(connection, watcher.notice)
                    notice = watcher.notice.split()[1]
                    self._logger.info(
                        "event=watch_notice connection_id=%d peer=%s notice=%s token_tag=%s",
                        connection_id,
                        peer,
                        notice,
                        token_tag(token),
                    )
                    return "watch_notice"
                try:
                    data = connection.recv(4096)
                    if not data:
                        return "watch_eof"
                    # WATCH owns this connection. Any later input is a protocol error.
                    self._send(connection, FORMAT_ERROR)
                    return "watch_protocol_error"
                except socket.timeout:
                    continue
            return "watch_timeout" if not self._stop.is_set() else "server_stop"
        finally:
            self.store.unwatch(token, watcher)


def main():
    parser = argparse.ArgumentParser(description="DDPP server")
    binding = parser.add_mutually_exclusive_group()
    binding.add_argument("--host", help="address to bind (default: 127.0.0.1)")
    binding.add_argument(
        "--lan",
        action="store_true",
        help="accept clients from this local network by binding to 0.0.0.0",
    )
    parser.add_argument("--port", type=int, default=5270)
    parser.add_argument("--max-clients", type=int, default=64)
    parser.add_argument("--log-file", default="ddpp-server.log")
    parser.add_argument("--log-max-bytes", type=int, default=1_000_000)
    parser.add_argument("--log-backups", type=int, default=3)
    args = parser.parse_args()
    host = "0.0.0.0" if args.lan else (args.host or "127.0.0.1")
    server = DDPPServer(
        host,
        args.port,
        max_clients=args.max_clients,
        log_file=args.log_file,
        log_max_bytes=args.log_max_bytes,
        log_backup_count=args.log_backups,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
