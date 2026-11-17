"""SOCK - multiple TCP sockets (firmware supports 4; id N uses peer port base + N)."""
import pytest

from pnet_lib.client import Pnet, token

pytestmark = [pytest.mark.functional, pytest.mark.requires('tcp_peer')]


@pytest.fixture
def ids(testbed):
    return list(range(testbed.peer.max_sockets))


@pytest.fixture
def all_open(online: Pnet, peer, tcp_addr, ids):
    for sid in ids:
        online.tcp_connect_id(sid, *tcp_addr(sid))
    return online


def _echo(pnet: Pnet, sid: int, peer=None) -> None:
    msg = token(f's{sid}')
    pnet.tcp_tx_id(sid, msg)
    if peer is not None:
        assert peer.tcp[sid].wait_received(msg.encode()), f'peer {sid} missed {msg}'
    pnet.tcp_rx_id(sid, msg)


@pytest.mark.scope_id('SOCK-001')
def test_all_sockets_echo(all_open: Pnet, peer, ids):
    for sid in ids:
        _echo(all_open, sid, peer)


@pytest.mark.negative
@pytest.mark.scope_id('SOCK-002')
@pytest.mark.requires('local_peer')
def test_extra_socket_rejected_when_full(all_open: Pnet, udp_addr, ids, peer):
    res = all_open.run(f'pnet udp_connect {udp_addr(0)[0]} {udp_addr(0)[1]}')
    if 'UDP connect successful' in res.text:
        all_open.state.udp_open = True
        pytest.fail('A 5th socket (UDP) was accepted while 4 TCP sockets were open')
    for sid in ids:                       # existing sockets unaffected
        _echo(all_open, sid, peer)


@pytest.mark.negative
@pytest.mark.scope_id('SOCK-003')
@pytest.mark.parametrize('bad_id', ['-1', '4', '99', 'abc'])
def test_invalid_socket_id(online: Pnet, tcp_addr, bad_id):
    res = online.run(f'pnet tcp_connect_id {bad_id} {tcp_addr(0)[0]} {tcp_addr(0)[1]}')
    assert 'TCP connect successful' not in res.text, res
    assert online.responsive()


@pytest.mark.scope_id('SOCK-004')
@pytest.mark.requires('local_peer')
def test_socket_isolation(all_open: Pnet, peer, ids):
    msgs = {sid: token(f'iso{sid}') for sid in ids}
    for sid, msg in msgs.items():
        all_open.tcp_tx_id(sid, msg)
    for sid, msg in msgs.items():
        assert peer.tcp[sid].wait_received(msg.encode())
        others = [m for s, m in msgs.items() if s != sid]
        assert not any(m.encode() in peer.tcp[sid].received for m in others), f'cross-talk on {sid}'
        all_open.tcp_rx_id(sid, msg)


@pytest.mark.scope_id('SOCK-005')
@pytest.mark.parametrize('victim', [0, 3])
def test_close_one_others_survive(all_open: Pnet, peer, ids, victim):
    all_open.tcp_disconnect_id(victim)
    for sid in ids:
        if sid != victim:
            _echo(all_open, sid, peer)


@pytest.mark.scope_id('SOCK-006')
def test_close_all_reopen_all(all_open: Pnet, peer, tcp_addr, ids):
    for sid in ids:
        all_open.tcp_disconnect_id(sid)
    for sid in ids:
        all_open.tcp_connect_id(sid, *tcp_addr(sid))
    for sid in ids:
        _echo(all_open, sid, peer)


@pytest.mark.scope_id('SOCK-007')
def test_reuse_id_while_others_active(all_open: Pnet, peer, tcp_addr, ids):
    for _ in range(5):
        all_open.tcp_disconnect_id(1)
        all_open.tcp_connect_id(1, *tcp_addr(1))
        for sid in ids:
            _echo(all_open, sid, peer)


@pytest.mark.stress
@pytest.mark.scope_id('SOCK-010')
def test_tx_loop_on_every_socket(all_open: Pnet, ids):
    for sid in ids:
        result = all_open.tcp_tx_loop_id(sid, 3, 100, 'PING')
        assert result.passed, result.result


@pytest.mark.recovery
@pytest.mark.scope_id('SOCK-012')
def test_iface_down_invalidates_all(all_open: Pnet, testbed, tcp_addr, ids, peer):
    all_open.iface_down()
    all_open.iface_up()
    all_open.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s,
                     post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    stale = all_open.run(f'pnet tcp_tx_id 0 {token("stale")}')
    assert 'TCP send successful' not in stale.text, 'Old socket still sends after link loss'
    for sid in ids:
        all_open.run(f'pnet tcp_disconnect_id {sid}')       # free slots if FW kept them
        all_open.tcp_connect_id(sid, *tcp_addr(sid))
        _echo(all_open, sid, peer)


@pytest.mark.power
@pytest.mark.scope_id('SOCK-013')
def test_multisocket_dns_across_ps_transitions(all_open: Pnet, testbed, peer, ids):
    for ps in (1, 0, 1):
        all_open.ps(ps)
        for sid in ids[:2]:
            _echo(all_open, sid, peer)
        assert all_open.resolve(testbed.dns.host, 'sync')
        assert all_open.resolve(testbed.dns.host, 'async')
