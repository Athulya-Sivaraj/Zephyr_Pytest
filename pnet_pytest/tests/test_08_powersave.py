"""PS - power-save behaviour (functional; current measurement lives in test_09)."""
import time

import pytest

from pnet_lib.client import Pnet, token

pytestmark = pytest.mark.power


@pytest.mark.scope_id('PS-001')
@pytest.mark.parametrize('state', [1, 0])
def test_ps_enable_disable(online: Pnet, testbed, state):
    online.ps(state)
    assert online.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('PS-002')
def test_psr_then_enable(online: Pnet, testbed):
    online.psr()
    online.ps(1)
    assert online.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('PS-003')
@pytest.mark.requires('tcp_peer')
@pytest.mark.parametrize('li', [1, 3, 10])
def test_listen_interval(online: Pnet, peer, tcp_addr, li):
    online.ps_li(li)
    online.ps(1)
    online.tcp_connect(*tcp_addr(0))
    msg = token(f'li{li}')
    online.tcp_tx(msg)
    online.tcp_rx(msg)


@pytest.mark.scope_id('PS-005')
@pytest.mark.requires('tcp_peer')
def test_disable_while_awake(online: Pnet, peer, tcp_addr):
    online.ps(1)
    online.tcp_connect(*tcp_addr(0))
    online.tcp_tx(token('busy'))            # module is awake right now
    start = time.monotonic()
    online.ps(0)
    assert time.monotonic() - start < 5, 'ps 0 while awake was slow'


@pytest.mark.scope_id('PS-006')
def test_disable_while_sleeping(online: Pnet, testbed):
    online.ps(1)
    time.sleep(testbed.ps.idle_before_wake_s)
    online.ps(0)
    assert online.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('PS-007')
@pytest.mark.requires('tcp_peer')
def test_host_tx_wakes_module(online: Pnet, peer, tcp_addr, testbed):
    online.ps(1)
    online.tcp_connect(*tcp_addr(0))
    time.sleep(testbed.ps.idle_before_wake_s)
    msg = token('wake')
    online.tcp_tx(msg)                     # must succeed first time
    if peer is not None:
        assert peer.tcp[0].wait_received(msg.encode())


@pytest.mark.scope_id('PS-008')
@pytest.mark.requires('local_peer')
def test_inbound_rx_wakes_module(online: Pnet, peer, tcp_addr, testbed):
    peer.tcp[0].set_mode('silent')
    online.ps(1)
    online.tcp_connect(*tcp_addr(0))
    time.sleep(testbed.ps.idle_before_wake_s)
    msg = token('inbound')
    peer.tcp[0].push(msg.encode())
    online.tcp_rx(msg)


@pytest.mark.scope_id('PS-009')
@pytest.mark.requires('local_peer')
@pytest.mark.parametrize('delay_ms', [0, 100, 250, 500, 1000, 2000])
def test_inbound_at_sleep_transition(online: Pnet, peer, tcp_addr, delay_ms):
    peer.tcp[0].set_mode('silent')
    online.ps(1)
    online.tcp_connect(*tcp_addr(0))
    online.tcp_tx(token('arm'))            # last activity -> sleep timer starts
    time.sleep(delay_ms / 1000)
    msg = token('race')
    peer.tcp[0].push(msg.encode())
    online.tcp_rx(msg)


@pytest.mark.scope_id('PS-010')
@pytest.mark.requires('tcp_peer')
@pytest.mark.parametrize('interval_ms', [100, 300, 500, 700, 1000, 2000])
def test_traffic_interval_sweep(online: Pnet, peer, tcp_addr, interval_ms, record_property):
    """Known corner: ~500 ms traffic keeps the module from sleeping. Traffic must still work;
    pair with PWR-003 to see whether it actually sleeps."""
    online.ps(1)
    online.tcp_connect(*tcp_addr(0))
    count = max(10, 30_000 // interval_ms)
    result = online.test_tcp_loop(*tcp_addr(0), count, interval_ms, 'PS')
    record_property('loop_fields', result.fields)
    assert result.passed, result.result


@pytest.mark.scope_id('PS-011')
def test_ps_idempotent(online: Pnet, testbed):
    online.ps(1)
    online.ps(1)
    online.ps(0)
    online.ps(0)
    assert online.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('PS-011')
@pytest.mark.requires('tcp_peer')
@pytest.mark.parametrize('order', ['ps_first', 'tcp_first'])
def test_ps_vs_tcp_connect_order(online: Pnet, peer, tcp_addr, order):
    if order == 'ps_first':
        online.ps(1)
        online.tcp_connect(*tcp_addr(0))
    else:
        online.tcp_connect(*tcp_addr(0))
        msg = token('pre')
        online.tcp_tx(msg)
        online.tcp_rx(msg)
        online.ps(1)
    msg = token('post')
    online.tcp_tx(msg)
    online.tcp_rx(msg)


@pytest.mark.scope_id('PS-013')
def test_builtin_ps_toggle(online: Pnet):
    result = online.test_ps_toggle(3, 300, 300)
    assert result.passed, result.result
