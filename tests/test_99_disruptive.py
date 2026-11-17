"""Disruptive tests: reboot or fault the board. Named 99 so they run last; the
fixtures re-initialise and reconnect lazily if anything runs after them."""
import pytest

from pnet_lib.client import Pnet, token

pytestmark = pytest.mark.disruptive


@pytest.mark.scope_id('BU-006')
def test_reset_then_full_bringup(pnet: Pnet, testbed):
    pnet.reset()
    pnet.init()
    pnet.fw_version()
    if testbed.wifi.ssid:
        pnet.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s,
                     post_connect_delay_s=testbed.wifi.post_connect_delay_s)
        assert pnet.resolve(testbed.dns.host, 'sync')


@pytest.mark.recovery
@pytest.mark.scope_id('REC-008')
@pytest.mark.requires('tcp_peer')
def test_host_reset_frees_ncp_sockets(online: Pnet, peer, tcp_addr, testbed):
    for sid in range(testbed.peer.max_sockets):
        online.tcp_connect_id(sid, *tcp_addr(sid))
    online.reset()
    online.init()
    online.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s,
                   post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    for sid in range(testbed.peer.max_sockets):     # all 4 slots must be free again
        online.tcp_connect_id(sid, *tcp_addr(sid))
        msg = token(f'r{sid}')
        online.tcp_tx_id(sid, msg)
        online.tcp_rx_id(sid, msg)


@pytest.mark.recovery
@pytest.mark.scope_id('REC-009')
@pytest.mark.requires('allow_assert')
def test_assert_recovery(pnet: Pnet):
    pnet.sh.expect_reboot = True
    pnet.sh.dut.write(b'pnet assert\n')
    pnet.state.rebooted()
    pnet.sh.wait_reboot(timeout=60)
    pnet.sh.fatal_lines.clear()                    # the assert itself is expected here
    pnet.init()
