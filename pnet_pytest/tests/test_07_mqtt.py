"""MQTT - broker from testbed.mqtt; host-side paho client verifies delivery."""
import time

import pytest

from pnet_lib.client import Pnet, token

pytestmark = [pytest.mark.functional, pytest.mark.requires('mqtt')]


@pytest.fixture
def mqtt(online: Pnet, testbed):
    online.mqtt_connect(testbed.mqtt.host, testbed.mqtt.port, f'{testbed.mqtt.client_id}-{token("c")[-4:]}')
    return online


def topic(testbed, name: str) -> str:
    return f'{testbed.mqtt.topic_prefix}/{name}/{token("t")[-8:]}'


@pytest.mark.scope_id('MQTT-001')
def test_connect_disconnect_plain(online: Pnet, testbed):
    online.mqtt_connect(testbed.mqtt.host, testbed.mqtt.port, testbed.mqtt.client_id)
    online.mqtt_disconnect()


@pytest.mark.scope_id('MQTT-002')
@pytest.mark.requires('mqtt_tls')
def test_connect_tls(online: Pnet, testbed):
    pytest.skip('pnet mqtt_connect has no TLS option yet (FW-dep)')


@pytest.mark.negative
@pytest.mark.scope_id('MQTT-005')
@pytest.mark.parametrize('host,port', [('no-such-broker.invalid', 1883), (None, 1)],
                         ids=['bad_host', 'bad_port'])
def test_connect_invalid_broker(online: Pnet, testbed, host, port):
    res = online.run(f'pnet mqtt_connect "{host or testbed.mqtt.host}" {port} pnet-bad', timeout=120)
    assert 'MQTT connect successful' not in res.text, res
    assert online.responsive()


@pytest.mark.scope_id('MQTT-007')
@pytest.mark.requires('host_mqtt')
@pytest.mark.parametrize('pub_qos', [0, 1])
@pytest.mark.parametrize('sub_qos', [0, 1])
def test_qos_matrix_dut_publishes(mqtt: Pnet, mqtt_host_client, testbed, pub_qos, sub_qos):
    t = topic(testbed, 'qos')
    mqtt_host_client.subscribe(t, sub_qos)
    time.sleep(1)
    msg = token('q')
    mqtt.mqtt_publish(t, msg, pub_qos)
    assert mqtt_host_client.wait_message(t, msg), f'host never received {msg} on {t}'


@pytest.mark.scope_id('MQTT-009')
@pytest.mark.requires('host_mqtt')
@pytest.mark.parametrize('qos', [0, 1])
def test_inbound_message(mqtt: Pnet, mqtt_host_client, testbed, qos):
    t = topic(testbed, 'in')
    mqtt.mqtt_subscribe(t, qos)
    msg = token('in')
    mqtt_host_client.publish(t, msg, qos)
    mqtt.sh.wait_for(msg, timeout=20)


@pytest.mark.scope_id('MQTT-010')
@pytest.mark.requires('host_mqtt')
@pytest.mark.parametrize('n_topics', [2, 4])
def test_multiple_topics(mqtt: Pnet, mqtt_host_client, testbed, n_topics):
    topics = [topic(testbed, f'multi{i}') for i in range(n_topics)]
    for t in topics:
        mqtt.mqtt_subscribe(t, 1)
    for t in topics:
        msg = token('m')
        mqtt_host_client.publish(t, msg, 1)
        mqtt.sh.wait_for(msg, timeout=20)


@pytest.mark.scope_id('MQTT-013')
@pytest.mark.requires('host_mqtt')
@pytest.mark.parametrize('interval_s', [0.1, 0.5, 1, 5])
@pytest.mark.parametrize('qos', [0, 1])
def test_publish_interval(mqtt: Pnet, mqtt_host_client, testbed, interval_s, qos, record_property):
    t = topic(testbed, 'rate')
    mqtt_host_client.subscribe(t, 1)
    time.sleep(1)
    n = 10
    for i in range(n):
        mqtt.mqtt_publish(t, f'n{i}', qos)
        time.sleep(interval_s)
    time.sleep(3)
    got = mqtt_host_client.count(t)
    record_property('mqtt_received', f'{got}/{n}')
    if qos == 1:
        assert got == n, f'QoS1: host received {got}/{n}'
    else:
        assert got >= n * 0.9, f'QoS0: host received only {got}/{n}'


@pytest.mark.scope_id('MQTT-014')
def test_connect_disconnect_cycles(online: Pnet, testbed):
    for i in range(10):
        online.mqtt_connect(testbed.mqtt.host, testbed.mqtt.port, f'{testbed.mqtt.client_id}-{i}')
        online.mqtt_disconnect()


@pytest.mark.recovery
@pytest.mark.scope_id('MQTT-015')
@pytest.mark.requires('host_mqtt')
def test_iface_down_then_mqtt_reconnect(mqtt: Pnet, mqtt_host_client, testbed):
    t = topic(testbed, 'recover')
    mqtt.mqtt_subscribe(t, 1)
    mqtt.iface_down()
    mqtt.iface_up()
    mqtt.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s,
                 post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    mqtt.run('pnet mqtt_disconnect', timeout=60)        # release stale client if FW kept it
    mqtt.mqtt_connect(testbed.mqtt.host, testbed.mqtt.port, f'{testbed.mqtt.client_id}-rec')
    mqtt.mqtt_subscribe(t, 1)
    msg = token('back')
    mqtt_host_client.publish(t, msg, 1)
    mqtt.sh.wait_for(msg, timeout=20)


@pytest.mark.power
@pytest.mark.scope_id('MQTT-017')
@pytest.mark.parametrize('ps', [0, 1])
def test_keepalive_idle(mqtt: Pnet, testbed, ps):
    mqtt.ps(ps)
    time.sleep(150)                        # > 2x a 60 s keepalive
    mqtt.mqtt_publish(topic(testbed, 'ka'), token('ka'), 1)


@pytest.mark.power
@pytest.mark.scope_id('MQTT-018')
@pytest.mark.requires('host_mqtt')
def test_pubsub_with_power_save(mqtt: Pnet, mqtt_host_client, testbed):
    t = topic(testbed, 'ps')
    mqtt.mqtt_subscribe(t, 1)
    mqtt_host_client.subscribe(t, 1)
    mqtt.ps(1)
    time.sleep(testbed.ps.idle_before_wake_s)
    out_msg = token('out')
    mqtt.mqtt_publish(t, out_msg, 1)
    assert mqtt_host_client.wait_message(t, out_msg)
    time.sleep(testbed.ps.idle_before_wake_s)
    in_msg = token('in')
    mqtt_host_client.publish(t, in_msg, 1)
    mqtt.sh.wait_for(in_msg, timeout=30)
