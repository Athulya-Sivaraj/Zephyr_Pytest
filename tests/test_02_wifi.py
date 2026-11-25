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


@pytest.fixture
def isolated_link(initialized: Pnet):
    _fresh_link(initialized)
    yield initialized
    # Even a failed connect may have started association. Don't leave an
    # alternate AP connected for fixtures that assume the primary AP.
    if initialized.sh.synchronized and not initialized.state.iface_down:
        initialized.iface_down()


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


@pytest.mark.scope_id('WIFI-003')
@pytest.mark.requires('secondary_wifi', 'secondary_bssid')
def test_connect_bssid_secondary(isolated_link: Pnet, testbed):
    wifi = testbed.wifi
    ap = wifi.secondary
    isolated_link.connect(ap.ssid, ap.psk, bssid=ap.bssid,
                          timeout=wifi.connect_timeout_s,
                          post_connect_delay_s=wifi.post_connect_delay_s)
    assert isolated_link.resolve(testbed.dns.host, 'sync')


@pytest.mark.scope_id('WIFI-004')
@pytest.mark.requires('open_network', 'open_bssid')
def test_connect_bssid_open(isolated_link: Pnet, testbed):
    wifi = testbed.wifi
    isolated_link.connect(wifi.open_ssid, bssid=wifi.open_bssid,
                          timeout=wifi.connect_timeout_s,
                          post_connect_delay_s=wifi.post_connect_delay_s)
    assert isolated_link.resolve(testbed.dns.host, 'sync')


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


@pytest.mark.scope_id('WIFI-002')
@pytest.mark.parametrize('profile', [
    pytest.param('primary', marks=pytest.mark.requires('secured_wifi')),
    pytest.param('secondary', marks=pytest.mark.requires('secondary_wifi')),
    pytest.param('open', marks=pytest.mark.requires('open_network')),
])
def test_connect_configured_ap(isolated_link: Pnet, testbed, profile):
    wifi = testbed.wifi
    ssid, psk = {
        'primary': (wifi.ssid, wifi.psk),
        'secondary': (wifi.secondary.ssid, wifi.secondary.psk),
        'open': (wifi.open_ssid, ''),
    }[profile]
    isolated_link.connect(ssid, psk, timeout=wifi.connect_timeout_s)


@pytest.mark.scope_id('WIFI-005')
@pytest.mark.requires('secondary_wifi')
def test_connect_minimum_length_psk(isolated_link: Pnet, testbed):
    wifi = testbed.wifi
    ap = wifi.secondary
    if len(ap.psk.encode('utf-8')) != 8:
        pytest.skip('WIFI-005 minimum-length case needs an 8-byte PSK on wifi.secondary')
    isolated_link.connect(ap.ssid, ap.psk, timeout=wifi.connect_timeout_s)


# WIFI-011 AP reboot, WIFI-012 blacklist, WIFI-013 deauth -> need an `ap` fixture
# (smart plug / AP API). Add them here with @pytest.mark.requires('ap_control').
