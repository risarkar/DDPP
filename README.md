# DDPP - Dead Drop Password Protocol
Short-Lived Password Sharing over TCP

DDPP is a lightweight client-server application for sharing short-lived credentials without leaving them permanently in chat or email history. A sender stores a password temporarily and receives a random token. The password can be retrieved only for its configured number of reads and is removed automatically when consumed or expired.

Team Group 3: Rishiraj Sarkar, Aakash Sivashanmugam, and Vishal Kumar.

Python 3.10+ standard-library implementation of the Dead Drop Password Protocol. No installation dependencies are needed. On macOS, use `python3` if `python` is unavailable.

## How DDPP works

1. A sender stores a password with a time to live and maximum read count.
2. The server returns a cryptographically random token. The sender shares this token through a separate channel.
3. A recipient uses the token to retrieve the password. Each successful retrieval consumes one allowed read.
4. The server removes the password after its final allowed read or when its time to live expires.
5. An optional `WATCH` connection reports when the final read occurs or the password expires.

The server keeps the shared in-memory password store, so the sender and recipient can use different computers. Possession of the token is the only authorization in this version.

## Local use

Download or clone this repository and open a terminal in its root folder, which contains `ddpp` and `tests`. No compilation or package installation is required.

Start the server:

```powershell
python -m ddpp.server
```

In other terminals, use the client:

```powershell
python -m ddpp.client store 3600 1
python -m ddpp.client info TOKEN
python -m ddpp.client watch TOKEN
python -m ddpp.client retrieve TOKEN
python -m ddpp.client quit
```

`store` prompts for the password without echoing it. Copy the returned token into the other commands. Keep `watch` running in one terminal and run `retrieve` in another. Host and port may be set with `--host` and `--port` before the subcommand. The default is `127.0.0.1:5270`.

To test concurrency, start two `retrieve` commands for a one-read token at the same time. Exactly one should print the password and the other should print `404 not found`.

## Persistent sessions and WATCH

For several ordinary commands on one connection, run:

```text
python -m ddpp.client session
```

At the `DDPP>` prompt, enter `store 600 1` and supply the password at the hidden prompt. Copy the returned token and use `info TOKEN` or `retrieve TOKEN`. INFO does not consume a read. Enter `quit` to receive `200 OK bye` and close the session. `help` lists guidance. End-of-input or Ctrl+C at the command prompt also requests QUIT. The original one-command client remains available.

WATCH runs in a separate terminal. Entering `watch` inside a session displays guidance instead of starting a subscription. We keep watching separate because it waits for an asynchronous notice, while the ordinary session remains available for other commands. A protocol connection that enters WATCH accepts only QUIT until its notice or closure. Other input receives 300 and leaves the subscription active.

Start the watch while the token is active, and wait for its acknowledgement before the final retrieval:

```text
python -m ddpp.client watch TOKEN --timeout 90
```

The optional positive waiting limit starts after `200 OK watching`. Press Ctrl+C to cancel. A timeout closes this watch without changing the password or proving whether the token exists. Without `--timeout`, waiting continues until a notice, cancellation, server closure, or the server's maximum watch lifetime. Notices are not replayed for events that occurred before subscription. A well-formed unknown token receives the same initial acknowledgement as a live token.

For remote use, include the same `--host SERVER_IP --port 5270` before `session` or `watch` in each terminal.

## Command-line options

Server options are `--host ADDRESS` (default `127.0.0.1`) or `--lan` (bind `0.0.0.0`), `--port PORT` (5270), `--max-clients COUNT` (64), `--log-file PATH` (`ddpp-server.log`), `--log-max-bytes COUNT` (1000000), and `--log-backups COUNT` (3). `--host` and `--lan` are mutually exclusive. Use positive operational values for capacity and logging settings.

Client `--host ADDRESS` and `--port PORT` options precede the subcommand. Subcommands are `store TTL MAX_READS`, `retrieve TOKEN`, `info TOKEN`, `watch TOKEN [--timeout SECONDS]`, `quit`, and `session`. The WATCH limit must be positive and finite. Both applications support `--help`.

## Sharing on a private local network

The server computer and all client computers must be connected to the same trusted Wi-Fi or Ethernet network. Start the server in LAN mode:

```powershell
python -m ddpp.server --lan --port 5270 --log-file ddpp-server.log
```

LAN mode binds the server to all local IPv4 interfaces. At startup, the server prints one or more addresses that clients can try. On each client computer, run commands from a copy or clone of this repository and replace `SERVER_IP` with the appropriate address:

```powershell
python -m ddpp.client --host SERVER_IP --port 5270 store 3600 1
python -m ddpp.client --host SERVER_IP --port 5270 info TOKEN
python -m ddpp.client --host SERVER_IP --port 5270 watch TOKEN
python -m ddpp.client --host SERVER_IP --port 5270 retrieve TOKEN
```

For example, one client can keep `WATCH` open while another client retrieves the same token. The watching client receives `210 BURNED` after the final allowed retrieval.

The operating-system firewall must permit inbound TCP connections to the selected port on the private network. On Windows, first allow Python through the private-network firewall prompt. If a client still cannot connect, test the connection from that computer:

```powershell
Test-NetConnection SERVER_IP -Port 5270
```

Some guest Wi-Fi networks prevent devices from communicating with one another. In that case, use a private network without client isolation.

## Server logs

The server writes UTC interaction records to `ddpp-server.log` by default. Logs include connection IDs, client addresses, commands, result codes, WATCH notices, expiration sweeps, and shutdown events. Passwords and complete tokens are never written to the log. Rotating backups limit log growth, and `--log-file` can select a different output path.

Stop the server cleanly with `Ctrl+C` before collecting the log for a demonstration or project submission.

## Tests

Run all 25 automated scenarios from the repository root:

```powershell
python -B -m unittest discover -s tests -v
```

The suite uses Python's standard-library unittest module. It retains the 19 scenarios in `tests/test_ddpp.py` and adds six in `tests/test_phase3.py`. The full run takes a little over a minute because one scenario waits for a real 60-second expiry. It covers all five commands, exact replies, framing, invalid input, WATCH recovery, read limits, shutdown, log privacy, persistent sessions, waiting limits, mixed concurrent operations, failure isolation, and ten clients completing 200 requests.

For only the six added scenarios, run `python -B -m unittest discover -s tests -p test_phase3.py -v`.

Tests print requests and responses using dummy passwords and save server logs to a new timestamped folder under `evidence/` on each run. The recorded coursework report, LAN screenshots, and collected submission evidence are supplied separately.

## Protocol summary

Each message is one UTF-8 line ending in `\n`. The line body may be at most 256 bytes, excluding LF. Commands and response fields use ASCII. Ordinary connections accept multiple commands. After `WATCH`, QUIT closes the session gracefully, while other input receives `300 message format error` and the subscription remains open.

| Command | Success | Errors |
| --- | --- | --- |
| `STORE <ttl> <max_reads> <password>` | `200 OK <32-lowercase-hex-token>` | `300`, `301`, `302`, `303` |
| `RETRIEVE <token>` | `200 OK <password>` | `300`, `404` |
| `INFO <token>` | `200 OK reads_remaining=<n> expires_in=<seconds>` | `300`, `404` |
| `WATCH <token>` | `200 OK watching`, then an optional notice | `300` |
| `QUIT` | `200 OK bye`, then close | `300` for extra fields |

TTL is an integer from 60 to 604800 seconds.
The maximum read count is an integer from 1 to 10. Passwords match `[A-Za-z0-9]{6,64}`.
Tokens are 32 hexadecimal characters.
Bad field counts, unsupported commands, invalid UTF-8, and overlong lines receive `300 message format error`.
Present but invalid TTL, read count, or password receives `301 invalid TTL`, `302 invalid max-reads`, or `303 invalid password`, respectively. A missing field is a field-count error.

`RETRIEVE` and `INFO` return `404 not found` for unknown, expired, and consumed tokens. Expiration is checked on every lookup.

`INFO` does not consume a read. A successful retrieval consumes one read atomically.
On the final allowed read, the entry is removed and watchers get `210 BURNED <token> <UTC timestamp>`.
On expiry with reads remaining, watchers get `220 EXPIRED <token> <UTC timestamp>`. Timestamp format is `YYYY-MM-DDTHH:MM:SSZ`.

Every syntactically valid WATCH gets the identical immediate `200 OK watching`, even if the token is absent. A WATCH connection accepts QUIT for graceful disconnection; other input receives `300 message format error` while the subscription stays open. It can remain open for up to 604800 seconds. The implementation also sets a connection limit. A watcher can observe consumption; it cannot establish the recipient's identity or prove the password was displayed. If a client disconnects after retrieval, it does not reverse consumption.

## Security and limitations

This implementation uses raw TCP and an in-memory store. Use dummy passwords on localhost or a trusted lab network. Real network deployment requires authenticated transport encryption and additional abuse controls. A server restart loses all entries. Deletion means removal from the active server store, not erasure of copies held by clients or the operating system.

## External resources

The application and tests use only [Python's standard library](https://docs.python.org/3/library/). No external networking framework is required. The [course project instructions](https://github.com/guojinhua-umd/socketProgramming) define the coursework requirements. Phase 1 discussed Privnote and One-Time Secret as examples of short-lived sharing services. DDPP implements its own text protocol.
