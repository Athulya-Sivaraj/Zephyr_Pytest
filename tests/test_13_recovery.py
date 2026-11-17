"""REC - recovery that needs no extra lab hardware. AP / NCP-reset cases need fixtures for
that equipment and are listed in the README."""
import time

import pytest

from pnet_lib.client import Pnet, token

pytestmark = [pytest.mark.recovery, pytest.mark.requires('tcp_peer')]


@pytest.mark.scope_id('REC-003')
@pytest.mark.parametrize('ps', [0, 1], ids=['ps_off', 'ps_on'])
def test_wifi_drop_with_open_socket(online: Pnet, peer, tcp_addr, testbed, ps):
    online.ps(ps)
    online.tcp_connect(*tcp_addr(0))
    online.tcp_tx(token('before'))
    online.iface_down()
    online.iface_up()
    online.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s,
                   post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    online.run('pnet tcp_disconnect')               # stale handle, any clean reply
    online.tcp_connect(*tcp_addr(0))
    msg = token('after')
    online.tcp_tx(msg)
    online.tcp_rx(msg)


@pytest.mark.scope_id('REC-004')
@pytest.mark.requires('local_peer')
def test_peer_restart_mid_stream(online: Pnet, peer, tcp_addr):
    online.tcp_connect(*tcp_addr(0))
    online.tcp_tx(token('pre'))
    peer.tcp[0].stop()
    time.sleep(2)
    peer.tcp[0].start()
    online.run(f'pnet tcp_tx {token("lost")}')
    online.run('pnet tcp_disconnect')
    online.state.tcp_default_open = False
    online.tcp_connect(*tcp_addr(0))
    msg = token('restored')
    online.tcp_tx(msg)
    online.tcp_rx(msg)
