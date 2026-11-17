"""UDP socket."""
import pytest

from pnet_lib.client import Pnet, token

pytestmark = [pytest.mark.functional, pytest.mark.requires('tcp_peer')]


@pytest.mark.scope_id('UDP-001')
def test_udp_echo(online: Pnet, peer, udp_addr):
    msg = token('udp')
    online.udp_connect(*udp_addr(0))
    online.udp_tx(msg)
    if peer is not None:
        assert peer.udp[0].wait_received(msg.encode())
    online.udp_rx(msg, timeout_ms=10000)
    online.udp_disconnect()


@pytest.mark.scope_id('UDP-002')
@pytest.mark.parametrize('size', [1, 64, 200])
def test_udp_sizes(online: Pnet, peer, udp_addr, size):
    msg = token('u', size) if size >= 12 else 'y' * size
    online.udp_connect(*udp_addr(0))
    online.udp_tx(msg)
    online.udp_rx(msg)


@pytest.mark.negative
@pytest.mark.scope_id('UDP-003')
@pytest.mark.requires('local_peer')
def test_udp_rx_timeout(online: Pnet, peer, udp_addr):
    peer.udp[0].echo = False
    online.udp_connect(*udp_addr(0))
    online.udp_tx(token('noecho'))
    res = online.run('pnet udp_rx 2000', timeout=30)
    assert 'data:' not in res.text, res
    assert online.responsive()
