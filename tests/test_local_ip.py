import subprocess
from types import SimpleNamespace

from screenbridge import cli


def test_mac_pairing_uses_active_physical_interface_without_dns(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")

    def no_dns(*args, **kwargs):
        raise AssertionError("Mac startup must not wait for hostname DNS")

    def interfaces(command, **kwargs):
        assert command == ["/sbin/ifconfig"] and kwargs["timeout"] == 1
        return SimpleNamespace(
            stdout="""lo0: flags=8049<UP,LOOPBACK>
    inet 127.0.0.1 netmask 0xff000000
utun0: flags=8051<UP,RUNNING>
    inet 10.8.0.2 netmask 0xffffff00
    status: active
en0: flags=8863<UP,RUNNING>
    inet 169.254.1.2 netmask 0xffff0000
    status: inactive
en1: flags=8863<UP,RUNNING>
    inet 192.168.1.42 netmask 0xffffff00
    status: active
"""
        )

    monkeypatch.setattr(cli.subprocess, "run", interfaces)
    monkeypatch.setattr(cli.socket, "getaddrinfo", no_dns)
    assert cli.local_ip() == "192.168.1.42"


def test_mac_without_network_still_starts_locally(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    def offline(*args):
        raise OSError("No network")

    monkeypatch.setattr(cli.subprocess, "run", timeout)
    monkeypatch.setattr(cli.socket, "socket", offline)
    assert cli.local_ip() == "127.0.0.1"
