"""TCP - single (default) socket against the suite's own echo server."""
import re
import time

import pytest

from pnet_lib.client import Pnet, PnetCommandError, token

pytestmark = [pytest.mark.functional, pytest.mark.requires('tcp_peer')]


def _server_got(peer, data: str, sock: int = 0, timeout: float = 10) -> bool:
    """True if the local peer received `data` (always True with an external peer)."""
    return peer is None or peer.tcp[sock].wait_received(data.encode(), timeout)


@pytest.mark.scope_id('TCP-001')
def test_tcp_lifecycle(online: Pnet, peer, tcp_addr):
    msg = token('tcp')
    online.tcp_connect(*tcp_addr(0))
    online.tcp_tx(msg)
    assert _server_got(peer, msg), 'Server never received the payload'
    online.tcp_rx(msg)
    online.tcp_disconnect()


@pytest.mark.scope_id('TCP-002')
def test_tcp_immediate_reconnect(online: Pnet, peer, tcp_addr):
    online.tcp_connect(*tcp_addr(0))
    online.tcp_disconnect()
    online.tcp_connect(*tcp_addr(0))
    msg = token('again')
    online.tcp_tx(msg)
    online.tcp_rx(msg)


@pytest.mark.negative
@pytest.mark.scope_id('TCP-003')
def test_tcp_double_connect_and_disconnect(online: Pnet, peer, tcp_addr):
    online.tcp_connect(*tcp_addr(0))
    second = online.run(f'pnet tcp_connect {tcp_addr(0)[0]} {tcp_addr(0)[1]}')
    assert online.responsive(), second
    online.tcp_disconnect()
    online.run('pnet tcp_disconnect')            # second disconnect: any clean reply
    assert online.responsive()
    tx = online.run(f'pnet tcp_tx {token("closed")}')
    assert 'TCP send successful' not in tx.text, 'tcp_tx succeeded on a closed socket'
    online.tcp_connect(*tcp_addr(0))          # still usable afterwards


@pytest.mark.scope_id('TCP-004')
@pytest.mark.parametrize('size', [1, 16, 64, 128, 200])   # bounded by CONFIG_SHELL_CMD_BUFF_SIZE
def test_tcp_payload_sizes(online: Pnet, peer, tcp_addr, size):
    msg = token('p', size) if size >= 12 else 'x' * size
    online.tcp_connect(*tcp_addr(0))
    online.tcp_tx(msg)
    assert _server_got(peer, msg)
    online.tcp_rx(msg)


@pytest.mark.scope_id('TCP-006')
@pytest.mark.requires('local_peer')
def test_tcp_server_push(online: Pnet, peer, tcp_addr):
    peer.tcp[0].set_mode('silent')
    online.tcp_connect(*tcp_addr(0))
    msg = token('push')
    peer.tcp[0].push(msg.encode())
    online.tcp_rx(msg)


@pytest.mark.stress
@pytest.mark.scope_id('TCP-007')
@pytest.mark.parametrize('count,interval_ms,payload', [
    (3, 100, 'PING'),
    (500, 20, 'abc'),
    (1000, 10, 'abcdefghihello'),
    (2000, 5, 'pnet_stress_payload_0123456789'),
], ids=['3-100-short', '500-20-abc', '1000-10-mid', '2000-5-long'])
def test_tcp_loop(online: Pnet, peer, tcp_addr, count, interval_ms, payload):
    online.tcp_connect(*tcp_addr(0))
    result = online.test_tcp_loop(*tcp_addr(0), count, interval_ms, payload)
    assert result.passed, result.result


@pytest.mark.negative
@pytest.mark.scope_id('TCP-008')
@pytest.mark.requires('local_peer')
def test_tcp_connection_refused(online: Pnet, peer, tcp_addr):
    peer.tcp[0].stop()                          # nothing listening -> RST
    start = time.monotonic()
    res = online.run(f'pnet tcp_connect {tcp_addr(0)[0]} {tcp_addr(0)[1]}', timeout=60)
    assert 'TCP connect successful' not in res.text, res
    assert time.monotonic() - start < 30, 'Refused connect took too long'
    assert online.responsive()


@pytest.mark.negative
@pytest.mark.scope_id('TCP-009')
def test_tcp_unreachable_host(online: Pnet, testbed):
    ip = testbed.peer.ip.rsplit('.', 1)[0] + '.254'
    res = online.run(f'pnet tcp_connect {ip} 9', timeout=120)
    assert 'TCP connect successful' not in res.text, res
    assert online.responsive()


@pytest.mark.recovery
@pytest.mark.scope_id('TCP-010')
@pytest.mark.requires('local_peer')
def test_tcp_remote_close(online: Pnet, peer, tcp_addr):
    online.tcp_connect(*tcp_addr(0))
    peer.tcp[0].wait_connected()
    peer.tcp[0].close_active(rst=False)
    time.sleep(2)
    tx = online.run(f'pnet tcp_tx {token("afterfin")}')
    rx = online.run('pnet tcp_rx', timeout=30)
    assert online.responsive(), f'{tx}\n{rx}'
    online.run('pnet tcp_disconnect')
    online.state.tcp_default_open = False
    online.tcp_connect(*tcp_addr(0))
    msg = token('reconn')
    online.tcp_tx(msg)
    online.tcp_rx(msg)


@pytest.mark.recovery
@pytest.mark.scope_id('TCP-011')
@pytest.mark.requires('local_peer')
def test_tcp_remote_reset(online: Pnet, peer, tcp_addr):
    online.tcp_connect(*tcp_addr(0))
    peer.tcp[0].wait_connected()
    peer.tcp[0].close_active(rst=True)
    time.sleep(1)
    with pytest.raises(PnetCommandError):
        online.tcp_rx(timeout=30)
    online.run('pnet tcp_disconnect')
    online.state.tcp_default_open = False
    online.tcp_connect(*tcp_addr(0))


@pytest.mark.negative
@pytest.mark.scope_id('TCP-014')
@pytest.mark.requires('local_peer')
def test_tcp_rx_timeout_without_data(online: Pnet, peer, tcp_addr):
    peer.tcp[0].set_mode('silent')
    online.tcp_connect(*tcp_addr(0))
    res = online.run('pnet tcp_rx', timeout=180)
    assert not re.search(r'TCP recv:\s*\S', res.text), f'Data received from a silent peer\n{res}'
    peer.tcp[0].push(b'late-data')
    online.tcp_rx('late-data')
