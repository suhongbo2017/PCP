from phonectl.core.adb import parse_devices

SAMPLE = """List of devices attached
R5CT123ABC  device usb:1-1 product:panther model:Pixel_7 device:panther transport_id:1
emulator-5554 unauthorized transport_id:2
0123456789ab offline

"""


def test_parse_devices():
    devs = parse_devices(SAMPLE)
    assert [d.serial for d in devs] == ["R5CT123ABC", "emulator-5554", "0123456789ab"]
    assert devs[0].ready and devs[0].props["model"] == "Pixel_7"
    assert not devs[1].ready and devs[1].state == "unauthorized"
    assert devs[2].state == "offline"


def test_parse_devices_empty():
    assert parse_devices("List of devices attached\n\n") == []
