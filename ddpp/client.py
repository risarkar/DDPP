"""Plain-text DDPP command-line client."""

import argparse
import getpass
import math
import socket
import sys

from .protocol import ProtocolError, parse


def _receive_line(reader):
    data = reader.readline(258)
    if not data:
        raise ConnectionError("server closed the connection")
    if not data.endswith(b"\n"):
        raise ConnectionError("server sent an incomplete or oversized line")
    body = data[:-1]
    if body.endswith(b"\r"):
        body = body[:-1]
    if len(body) > 256:
        raise ConnectionError("server sent an incomplete or oversized line")
    return body.decode("utf-8")


def request(host, port, command, watch=False, watch_timeout=None):
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(None if watch else 10)
        with connection.makefile("rb") as reader:
            connection.sendall((command + "\n").encode("utf-8"))
            initial = _receive_line(reader)
            print(initial, flush=True)
            if watch and initial == "200 OK watching":
                guidance = "Waiting for final consumption or expiry. Press Ctrl+C to cancel."
                if watch_timeout is None:
                    guidance += " Use --timeout SECONDS to limit the wait."
                else:
                    connection.settimeout(watch_timeout)
                    guidance += f" The waiting limit is {watch_timeout:g} seconds."
                print(guidance, file=sys.stderr, flush=True)
                try:
                    print(_receive_line(reader), flush=True)
                except socket.timeout:
                    print(
                        "Waiting limit reached without a notice. This does not determine token validity.",
                        file=sys.stderr,
                    )
                except KeyboardInterrupt:
                    print("Watch cancelled")


def interactive_session(host, port):
    """Keep ordinary commands on one connection. WATCH uses another terminal."""
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(10)
        with connection.makefile("rb") as reader:
            print(f"Connected to {host}:{port}", flush=True)
            print("Commands: store TTL READS, info TOKEN, retrieve TOKEN, quit. Type help for guidance.")
            while True:
                try:
                    value = input("DDPP> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    value = "quit"
                fields = value.split()
                if not fields:
                    continue
                name = fields[0].lower()
                if name == "help":
                    print("STORE prompts for a password. INFO does not consume a read. QUIT closes this session.")
                    print(f"Run WATCH in another terminal: python -m ddpp.client --host {host} "
                          f"--port {port} watch TOKEN --timeout 60")
                    continue
                if name == "watch":
                    print("Run WATCH in a separate terminal. This connection remains available for ordinary commands.")
                    continue
                if name == "store":
                    if len(fields) != 3:
                        print("Usage: store TTL READS. The password is entered at the hidden prompt.")
                        continue
                    try:
                        password = getpass.getpass("Password (6-64 letters/digits): ")
                    except KeyboardInterrupt:
                        print("Password entry cancelled.")
                        continue
                    command = f"STORE {fields[1]} {fields[2]} {password}"
                else:
                    command = " ".join([name.upper(), *fields[1:]])
                try:
                    parsed = parse(command.encode("utf-8"))
                except ProtocolError as exc:
                    print(f"Input error: {exc.response}")
                    continue
                connection.sendall((command + "\n").encode("utf-8"))
                print(_receive_line(reader), flush=True)
                if parsed.name == "QUIT":
                    return


def positive_seconds(value):
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds")
    return seconds


def main():
    parser = argparse.ArgumentParser(description="DDPP client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5270)
    sub = parser.add_subparsers(dest="command", required=True)
    store = sub.add_parser("store")
    store.add_argument("ttl", type=int)
    store.add_argument("max_reads", type=int)
    for name in ("retrieve", "info"):
        sub.add_parser(name).add_argument("token")
    watch = sub.add_parser("watch")
    watch.add_argument("token")
    watch.add_argument("--timeout", type=positive_seconds, help="stop waiting after this many seconds without a notice")
    sub.add_parser("quit")
    sub.add_parser("session", help="run ordinary commands on one persistent connection")
    args = parser.parse_args()
    if args.command == "store":
        password = getpass.getpass("Password (6-64 letters/digits): ")
        command = f"STORE {args.ttl} {args.max_reads} {password}"
    elif args.command == "quit":
        command = "QUIT"
    else:
        command = f"{args.command.upper()} {args.token}" if args.command != "session" else None
    try:
        if args.command == "session":
            interactive_session(args.host, args.port)
        else:
            request(args.host, args.port, command, watch=args.command == "watch",
                    watch_timeout=getattr(args, "timeout", None))
    except (ConnectionError, OSError, UnicodeError) as exc:
        parser.exit(1, f"DDPP connection error: {exc}\n")


if __name__ == "__main__":
    main()
