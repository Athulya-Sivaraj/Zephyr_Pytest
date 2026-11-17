"""WIFI - Wi-Fi connectivity."""
import time

import pytest

from pnet_lib.client import Pnet, shell_argument

pytestmark = pytest.mark.functional


def _fresh_link(pnet: Pnet) -> None:
    """Drop any existing association so the test measures a real connect."""
    if pnet.state.connected or pnet.state.iface_down:
        if not pnet.state.iface_down:
            pnet.iface_down()
        pnet.iface_up()


@pytest.mark.scope_id('WIFI-001')
@pytest.mark.requires('wifi')
def test_connect_wpa2(initialized: Pnet, testbed, record_property):
    _fresh_link(initialized)
    start = time.monotonic()
    initialized.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s)
    record_property('connect_to_ip_s', round(time.monotonic() - start, 2))
    time.sleep(testbed.wifi.post_connect_delay_s)
    assert initialized.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('WIFI-003')
@pytest.mark.requires('wifi', 'bssid')
def test_connect_bssid_secured(initialized: Pnet, testbed):
    _fresh_link(initialized)
    initialized.connect(testbed.wifi.ssid, testbed.wifi.psk, bssid=testbed.wifi.bssid,
                        timeout=testbed.wifi.connect_timeout_s,
                        post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    assert initialized.resolve(testbed.dns.host, 'sync')
    initialized.ps(1)


@pytest.mark.scope_id('WIFI-004')
@pytest.mark.requires('open_network')
def test_connect_bssid_open(initialized: Pnet, testbed):
    _fresh_link(initialized)
    initialized.connect(testbed.wifi.open_ssid, bssid=testbed.wifi.open_bssid,
                        timeout=testbed.wifi.connect_timeout_s,
                        post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    assert initialized.resolve(testbed.dns.host, 'sync')
    initialized.state.connected = False        # next test must reconnect to the main AP
    initialized.iface_down()


@pytest.mark.scope_id('WIFI-006')
def test_connect_when_already_connected(online: Pnet, testbed):
    args = ' '.join(shell_argument(a) for a in (testbed.wifi.ssid, testbed.wifi.psk) if a)
    online.run(f'pnet connect {args}', 30, print_output=False)
    time.sleep(testbed.wifi.post_connect_delay_s)
    assert online.responsive()
    online.sh.check_fatal()
    # Whatever the firmware decided, the link must still be usable.
    assert online.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('WIFI-009')
def test_iface_cycle_then_reconnect(online: Pnet, testbed):
    online.iface_down()
    online.iface_up()
    online.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s,
                   post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    assert online.resolve(testbed.dns.host, 'sync')


@pytest.mark.negative
@pytest.mark.scope_id('WIFI-014')
@pytest.mark.requires('wifi')
def test_wrong_psk_then_correct(initialized: Pnet, testbed):
    if not testbed.wifi.psk:
        pytest.skip('Open network configured; wrong-PSK test needs a secured AP')
    _fresh_link(initialized)
    assert not initialized.try_connect(testbed.wifi.ssid, 'wrongpass123', timeout=45)
    assert initialized.responsive()
    _fresh_link(initialized)
    initialized.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s)


@pytest.mark.negative
@pytest.mark.scope_id('WIFI-015')
@pytest.mark.requires('wifi')
def test_nonexistent_ssid_then_correct(initialized: Pnet, testbed):
    _fresh_link(initialized)
    assert not initialized.try_connect('pnet-no-such-ssid-42', 'whatever123', timeout=45)
    assert initialized.responsive()
    _fresh_link(initialized)
    initialized.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s)


# WIFI-011 AP reboot, WIFI-012 blacklist, WIFI-013 deauth -> need an `ap` fixture
# (smart plug / AP API). Add them here with @pytest.mark.requires('ap_control').
