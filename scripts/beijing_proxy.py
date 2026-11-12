#!/usr/bin/env python3
"""Private CONNECT proxy using this host's real network, without upstream proxies.

Only loopback clients with the generated credential can open HTTPS tunnels.
TLS remains end-to-end; request contents and credentials are never logged.
"""
import argparse
import asyncio
import base64
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UNIT = "short-video-ai-factory-beijing-proxy.service"
PORT = 18791


async def public_addresses(host, port):
    records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM), timeout=15)
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ValueError("Private destinations are not supported")
    return sorted(addresses, key=lambda address: ":" in address)


def destination(authority):
    host, sep, port = authority.rpartition(":")
    if not sep or not host or port != "443" or any(c in host for c in "/@?#\\"):
        raise ValueError("Only explicit HTTPS destinations on port 443 are supported")
    host = host.removeprefix("[").removesuffix("]")
    return host, 443


async def relay(reader, writer):
    while data := await asyncio.wait_for(reader.read(65536), timeout=300):
        writer.write(data)
        await writer.drain()


class Proxy:
    def __init__(self, token, *, dial=asyncio.open_connection):
        self.authorization = "Basic " + base64.b64encode(("factory:" + token).encode()).decode()
        self.capacity = asyncio.Semaphore(64)
        self.dial = dial

    async def handle(self, reader, writer):
        upstream = None
        connected = False
        tasks = []
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
            lines = header.decode("iso-8859-1").split("\r\n")
            method, target, version = lines[0].split(" ")
            if version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise ValueError("Invalid HTTP version")
            if method == "GET" and target == "/health":
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{"status":"ok"}')
                await writer.drain()
                return
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, value = line.split(":", 1)
                if key.lower() in headers:
                    raise ValueError("Duplicate header")
                headers[key.lower()] = value.strip()
            if not hmac.compare_digest(headers.get("proxy-authorization", "").encode("iso-8859-1"), self.authorization.encode("ascii")):
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="factory"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                await writer.drain()
                return
            if method != "CONNECT":
                raise ValueError("Only CONNECT is supported")
            host, port = destination(target)
            async with self.capacity:
                addresses = await public_addresses(host, port)
                for address in addresses[:4]:
                    try:
                        remote_reader, upstream = await asyncio.wait_for(
                            self.dial(address, port, limit=65536), timeout=15)
                        break
                    except OSError:
                        continue
                if upstream is None:
                    raise OSError("Destination unavailable")
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                connected = True
                tasks = [asyncio.create_task(relay(reader, upstream)),
                         asyncio.create_task(relay(remote_reader, writer))]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not connected and not writer.is_closing():
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), timeout=3)
                    except (OSError, asyncio.TimeoutError):
                        pass


async def serve(port):
    token = os.environ.get("FACTORY_BEIJING_PROXY_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("Private proxy credential is missing")
    server = await asyncio.start_server(Proxy(token).handle, "127.0.0.1", port, limit=32768, backlog=64)
    async with server:
        await server.serve_forever()


def install():
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    env = runtime / "beijing-proxy.env"
    if not env.exists():
        env.write_text("FACTORY_BEIJING_PROXY_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    env.chmod(0o600)
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    python = ROOT / ".venv/bin/python"
    unit = directory / UNIT
    unit.write_text(f'''[Unit]
Description=Content Factory private Beijing egress proxy
After=network-online.target

[Service]
Type=simple
EnvironmentFile={env}
WorkingDirectory={ROOT}
ExecStart={json.dumps(str(python))} {json.dumps(str(Path(__file__).resolve()))} --port {PORT}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=default.target
''')
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=True, capture_output=True)
    print("Private loopback proxy installed; credentials remain in runtime storage")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--port", type=int, default=PORT)
    arguments = parser.parse_args()
    if arguments.install:
        install()
    else:
        asyncio.run(serve(arguments.port))
