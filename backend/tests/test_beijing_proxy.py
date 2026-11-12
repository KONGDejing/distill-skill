"""Proxy authentication, destination isolation and byte-preserving CONNECT."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('factory_beijing_proxy', Path(__file__).resolve().parents[2] / 'scripts/beijing_proxy.py')
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


@pytest.mark.parametrize('value', ['example.test:80', 'example.test', 'user@example.test:443', 'example.test/path:443'])
def test_only_explicit_https_destinations_are_supported(value):
    with pytest.raises(ValueError):
        proxy.destination(value)


def test_private_network_destinations_are_rejected_after_dns_resolution():
    async def run():
        for host in ['127.0.0.1', '::1', '10.0.0.1', '169.254.169.254', '224.0.0.1']:
            with pytest.raises(ValueError, match='Private'):
                await proxy.public_addresses(host, 443)
    asyncio.run(run())


def test_proxy_requires_credentials_before_opening_any_tunnel():
    async def run():
        opened = []
        async def forbidden(*args, **kwargs):
            opened.append(args)
            raise AssertionError('Unauthorized tunnel')
        handler = proxy.Proxy('fixture-credential', dial=forbidden)
        async with await asyncio.start_server(handler.handle, '127.0.0.1', 0, limit=32768) as server:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            writer.write(b'CONNECT example.test:443 HTTP/1.1\r\nHost: example.test\r\n\r\n')
            await writer.drain()
            assert b'407 Proxy Authentication Required' in await reader.read()
            writer.close()
            await writer.wait_closed()
        assert opened == []
    asyncio.run(run())


def test_connect_preserves_bytes_and_does_not_forward_proxy_credentials(monkeypatch):
    async def run():
        received = []
        async def echo(reader, writer):
            try:
                while data := await reader.read(1024):
                    received.append(data)
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        async with await asyncio.start_server(echo, '127.0.0.1', 0) as upstream:
            port = upstream.sockets[0].getsockname()[1]
            async def addresses(*args):
                return ['93.184.215.14']
            monkeypatch.setattr(proxy, 'public_addresses', addresses)
            async def dial(address, requested_port, **kwargs):
                assert address == '93.184.215.14' and requested_port == 443
                return await asyncio.open_connection('127.0.0.1', port)
            handler = proxy.Proxy('fixture-credential', dial=dial)
            async with await asyncio.start_server(handler.handle, '127.0.0.1', 0, limit=32768) as server:
                reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
                writer.write(('CONNECT example.test:443 HTTP/1.1\r\nProxy-Authorization: ' + handler.authorization + '\r\n\r\n').encode())
                await writer.drain()
                assert b'200 Connection Established' in await reader.readuntil(b'\r\n\r\n')
                message = bytes(range(256))
                writer.write(message)
                await writer.drain()
                assert await reader.readexactly(len(message)) == message
                writer.close()
                await writer.wait_closed()
                await asyncio.sleep(.01)
            assert b''.join(received) == message
    asyncio.run(run())
