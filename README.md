# DDPP
One time read Password sharing over TCP

Python 3.10+ standard-library implementation of the Dead Drop Password protocol. No installation dependencies are needed.

From this folder, start the server:

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

Run tests from this folder:

```powershell
python -m unittest discover -s tests -v
```

Each message is one UTF-8 line ending in `\n`. The line body may be at most 256 bytes. Commands and response fields use ASCII. Clients may send multiple commands on one connection, except `WATCH`, which takes over that connection until a notice or disconnect.

| Command | Success | Errors |
| --- | --- | --- |
| `STORE <ttl> <max_reads> <password>` | `200 OK <32-lowercase-hex-token>` | `300`, `301`, `302`, `303` |
| `RETRIEVE <token>` | `200 OK <password>` | `300`, `404` |
| `INFO <token>` | `200 OK reads_remaining=<n> expires_in=<seconds>` | `300`, `404` |
| `WATCH <token>` | `200 OK watching`, then an optional notice | `300` |
| `QUIT` | `200 OK bye`, then close | `300` for extra fields |

TTL is an integer from 60 to 604800 seconds. Maximum reads is an integer from 1 to 10. Passwords match `[A-Za-z0-9]{6,64}`. Tokens are 32 hexadecimal characters. Bad field counts, unsupported commands, invalid UTF-8, and overlong lines receive `300 message format error`. Present but invalid TTL, read count, or password receives `301 invalid TTL`, `302 invalid max-reads`, or `303 invalid password`, respectively. A missing field is a field-count error.

`RETRIEVE` and `INFO` return `404 not found` for unknown, expired, and consumed tokens. Expiration is checked on every lookup. `INFO` does not consume a read. A successful retrieval consumes one read atomically. On the final allowed read, the entry is removed and watchers get `210 BURNED <token> <UTC timestamp>`. On expiry with reads remaining, watchers get `220 EXPIRED <token> <UTC timestamp>`. Timestamp format is `YYYY-MM-DDTHH:MM:SSZ`.

Every syntactically valid WATCH gets the identical immediate `200 OK watching`, even if the token is absent. A WATCH connection accepts no further commands and can remain open for up to 604800 seconds. The implementation also sets a connection limit. A watcher can observe consumption; it cannot establish the recipient's identity or prove the password was displayed. A client disconnect after retrieval does not reverse consumption.

This implementation uses raw TCP and an in-memory store. Use dummy passwords on localhost or a trusted lab network. Real network deployment requires authenticated transport encryption and additional abuse controls. A server restart loses all entries. Deletion means removal from the active server store, not erasure of copies held by clients or the operating system.
