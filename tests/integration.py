#!/usr/bin/env python3
"""Destructive-to-container integration suite: use only in a disposable systemd VM/container."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("manager", ROOT / "brook_pf.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.SOURCE = ROOT / "brook-pf.sh"


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(4096))


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, connection = self.request
        connection.sendto(data, self.client_address)


class TCP6(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6


class UDP6(socketserver.ThreadingUDPServer):
    address_family = socket.AF_INET6


@contextlib.contextmanager
def echo_servers(address="127.0.0.1"):
    tcp_type, udp_type = ((TCP6, UDP6) if ":" in address else
                          (socketserver.ThreadingTCPServer, socketserver.ThreadingUDPServer))
    tcp = tcp_type((address, 0), TCP)
    udp = udp_type((address, tcp.server_address[1]), UDP)
    for server in (tcp, udp):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield tcp.server_address[1]
    finally:
        for server in (tcp, udp):
            server.shutdown()
            server.server_close()


def free_port(address="127.0.0.1"):
    with socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((address, 0))
        return sock.getsockname()[1]


def probe(number, address="127.0.0.1"):
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, kind) as sock:
            sock.settimeout(4)
            sock.connect((address, number))
            message = b"brook-pf real TCP and UDP integration"
            sock.sendall(message)
            assert sock.recv(4096) == message


def cli(*args, **kwargs):
    return subprocess.run(["bash", str(m.MANAGER)] + list(args), check=True, **kwargs)


def expect_failure(call):
    try:
        call()
    except Exception:
        return
    raise AssertionError("expected failure did not occur")


def legacy_fixture(local, target):
    shutil.copy2("/bin/bash", str(m.BIN))
    m.CONF.write_text("# legacy preserved backup\n{} 127.0.0.1 {} 1\n8080 example.com 80 0\n".format(local, target))
    if not m.INIT.parent.exists():
        if Path("/etc/rc.d/init.d").exists():
            m.INIT.parent.symlink_to("rc.d/init.d")
        else:
            m.INIT.parent.mkdir(parents=True)
    m.INIT.write_text('''#!/bin/sh
### BEGIN INIT INFO
# Provides: brook-pf
# Required-Start: $network
# Required-Stop: $network
# Default-Start: 2 3 4 5
# Default-Stop: 0 1 6
# Short-Description: Test-only legacy Brook fixture
### END INIT INFO
case "$1" in
start)
    /usr/local/brook-pf/brook -c 'while :; do sleep 1; done' relays &
    echo $! > /run/brook-pf-fixture.pid
    ;;
stop) kill "$(cat /run/brook-pf-fixture.pid)" 2>/dev/null || true ;;
esac
exit 0
''')
    m.INIT.chmod(0o755)
    # Use explicit links so migration works independently of SysV registration utilities.
    for level in (2, 3, 4, 5):
        folder = Path("/etc/rc{}.d".format(level))
        if not folder.exists() and Path("/etc/rc.d").exists():
            real = Path("/etc/rc.d/rc{}.d".format(level))
            real.mkdir(exist_ok=True)
            folder.symlink_to("rc.d/rc{}.d".format(level))
        else:
            folder.mkdir(exist_ok=True)
        (folder / "S99brook-pf").symlink_to("../init.d/brook-pf")
    cron = "# preserved comment\n17 3 * * * /bin/true\n* * * * * /bin/bash /root/brook-pf.sh monitor\n"
    m.run(["crontab", "-"], input=cron.encode())
    m.systemctl("daemon-reload")
    m.systemctl("start", m.SERVICE)
    assert m.legacy_pids()
    return m.CONF.read_bytes(), cron


def test_all():
    if os.environ.get("BROOK_PF_INTEGRATION") != "1" or os.geteuid() != 0:
        raise SystemExit("Run ONLY in a disposable systemd test environment with BROOK_PF_INTEGRATION=1")
    assert not m.BASE.exists(), "test requires a clean system"
    m.requirements()
    with tempfile.TemporaryDirectory() as directory, echo_servers() as target:
        directory = Path(directory)
        cached = os.environ.get("BROOK_PF_TEST_BINARY")
        if cached:
            metadata = json.loads(Path(os.environ["BROOK_PF_TEST_RELEASE"]).read_text())
            tag, url, digest = m.release_asset(metadata, m.architecture())
            candidate = Path(cached)
            m.verify_binary(candidate, digest)
            info = {"version": tag, "sha256": digest, "asset": url, "manager": m.VERSION}
        else:
            candidate, info = m.stage_binary(directory)
        original_stage = lambda *args, **kwargs: (candidate, info)
        broken = directory / "broken"
        broken.write_text("#!/bin/sh\nexit 42\n")
        broken.chmod(0o755)
        local = free_port()
        with mock.patch.object(m, "stage_binary", side_effect=original_stage):
            print("TEST fresh install + interactive menu + TCP/UDP", flush=True)
            m.install()
            assert m.active() and m.enabled()
            cli("menu", input=b"0\n")
            cli("add", str(local), "127.0.0.1", str(target), "--listen", "127.0.0.1")
            probe(local)
            cli("set", str(local), "localhost", str(target), "--listen", "127.0.0.1")
            probe(local)
            print("TEST disabled rule + same-port edit + IPv6", flush=True)
            cli("disable", str(local))
            expect_failure(lambda: socket.create_connection(("127.0.0.1", local), timeout=1))
            cli("enable", str(local))
            probe(local)
            with echo_servers("::1") as ipv6_target:
                local6 = free_port("::1")
                cli("add", str(local6), "::1", str(ipv6_target), "--listen", "::1")
                probe(local6, "::1")
                cli("delete", str(local6))
            print("TEST occupied port rolls back config", flush=True)
            before = m.CONF.read_bytes()
            expect_failure(lambda: m.edit_rule("add", target, "127.0.0.1", local, "127.0.0.1"))
            assert m.CONF.read_bytes() == before
            probe(local)
            print("TEST stopped/disabled state preserved across update", flush=True)
            m.systemctl("disable", "--now", m.SERVICE)
            m.install(preserve_state=True)
            assert not m.active() and not m.enabled()
            m.systemctl("enable", "--now", m.SERVICE)
            print("TEST failed binary upgrade rolls back executable and service", flush=True)
            with mock.patch.object(m, "stage_binary", return_value=(broken, info)):
                expect_failure(m.install)
            assert m.BIN.read_bytes() == candidate.read_bytes()
            probe(local)
            print("TEST uninstall preserves config", flush=True)
            before = m.CONF.read_bytes()
            m.uninstall()
            assert m.CONF.read_bytes() == before and not m.UNIT.exists()
            print("TEST failed legacy migration restores init/cron/config/process", flush=True)
            old_conf, old_cron = legacy_fixture(local, target)
            with mock.patch.object(m, "stage_binary", return_value=(broken, info)):
                expect_failure(m.install)
            assert m.INIT.exists() and m.CONF.read_bytes() == old_conf
            assert m.get_cron() == old_cron and m.legacy_pids()
            print("TEST successful legacy migration preserves disabled rule + unrelated cron/process", flush=True)
            unrelated = directory / "unrelated-brook"
            shutil.copy2("/bin/bash", str(unrelated))
            other = subprocess.Popen([str(unrelated), "-c", "while :; do sleep 1; done", "relays"])
            try:
                m.install()
                assert other.poll() is None
                assert not m.INIT.exists() and not m.legacy_pids()
                assert m.get_cron() == m.without_old_monitor(old_cron)
                assert len(m.read_rules()) == 2 and m.read_rules()[1][3] == 0
                probe(local)
                assert any((folder / "1").read_bytes() == old_conf for folder in m.BACKUPS.iterdir()
                           if (folder / "1").exists())
            finally:
                other.terminate()
                other.wait(timeout=5)
            m.uninstall()
    print("PASS: all real systemd / TCP / UDP / IPv6 / rollback / migration checks", flush=True)


if __name__ == "__main__":
    test_all()
