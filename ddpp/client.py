"""Plain-text DDPP command-line client."""

import argparse
import getpass
import socket


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


def request(host, port, command, watch=False):
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(None if watch else 10)
        with connection.makefile("rb") as reader:
            connection.sendall((command + "\n").encode("utf-8"))
            initial = _receive_line(reader)
            print(initial)
            if watch and initial == "200 OK watching":
                try:
                    print(_receive_line(reader))
                except KeyboardInterrupt:
                    print("Watch cancelled")


def main():
    parser = argparse.ArgumentParser(description="DDPP client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5270)
    sub = parser.add_subparsers(dest="command", required=True)
    store = sub.add_parser("store")
    store.add_argument("ttl", type=int)
    store.add_argument("max_reads", type=int)
    for name in ("retrieve", "info", "watch"):
        sub.add_parser(name).add_argument("token")
    sub.add_parser("quit")
    args = parser.parse_args()
    if args.command == "store":
        password = getpass.getpass("Password (6-64 letters/digits): ")
        command = f"STORE {args.ttl} {args.max_reads} {password}"
    elif args.command == "quit":
        command = "QUIT"
    else:
        command = f"{args.command.upper()} {args.token}"
    try:
        request(args.host, args.port, command, watch=args.command == "watch")
    except (ConnectionError, OSError, UnicodeError) as exc:
        parser.exit(1, f"DDPP connection error: {exc}\n")


if __name__ == "__main__":
    main()
