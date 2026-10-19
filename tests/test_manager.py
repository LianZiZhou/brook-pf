import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("manager", ROOT / "brook_pf.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ConfigTests(unittest.TestCase):
    def test_legacy_crlf_comments_and_disabled(self):
        rules = m.parse_config("\r\n# header\r\n00080 1.2.3.4 443 1\r\n8080 example.com 80 0 # disabled")
        self.assertEqual(rules, [(80, "1.2.3.4", 443, 1, "0.0.0.0"), (8080, "example.com", 80, 0, "0.0.0.0")])
        self.assertEqual(m.parse_config(m.serialize(rules)), rules)

    def test_ipv6_and_domains(self):
        rule = m.parse_config("9000 [2001:db8::1] 443 1 ::1")[0]
        self.assertEqual(m.relay_args(rule)[2:], ["--from", "[::1]:9000", "--to", "[2001:db8::1]:443"])
        self.assertEqual(m.host("EXAMPLE.COM."), "example.com")
        self.assertEqual(m.host("例子.测试"), "xn--fsqu00a.xn--0zwm56d")

    def test_reject_malformed_and_injection(self):
        for text in ("0 x 80 1", "65536 x 80 1", "80 x 80 yes", "80 x 80", "80 x 80 1 extra extra",
                     "80 x 80 1\n080 x 81 0", "80 $(touch/tmp/pwn) 80 1", "80 x;id 80 1",
                     "80 --help 80 1", "80 999.2.3.4 80 1", "80 x 80 1 hostname", "1+2 x 80 1"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.parse_config(text)

    def test_exact_port_edits_and_duplicate_edit(self):
        rules = m.parse_config("80 a 80 1\n8080 b 80 1\n18080 c 80 1")
        with mock.patch.object(m, "read_rules", return_value=rules), mock.patch.object(m, "save_rules") as save:
            m.edit_rule("delete", "80")
            self.assertEqual([r[0] for r in save.call_args[0][0]], [8080, 18080])
        with mock.patch.object(m, "read_rules", return_value=rules), self.assertRaises(ValueError):
            m.edit_rule("add", "8080", "x", "443")

    def test_monitor_cleanup_only_matches_legacy_command(self):
        original = ("# /root/brook-pf.sh monitor\n* * * * * /bin/bash /root/brook-pf.sh monitor\n"
                    "* * * * * /root/other.sh monitor\n* * * * * /root/brook-pf.sh status\n"
                    "* * * * * echo /root/brook-pf.sh monitor\n")
        self.assertEqual(m.without_old_monitor(original), original.replace(
            "* * * * * /bin/bash /root/brook-pf.sh monitor\n", ""))
        self.assertEqual(m.without_old_monitor('* * * * * bash "/a b/brook-pf.sh" monitor\n'), "")


class ReleaseTests(unittest.TestCase):
    def metadata(self, digest=None):
        return {"tag_name": "v20270101", "assets": [{"name": "brook_linux_arm64",
                "browser_download_url": "https://github.com/txthinking/brook/releases/download/v20270101/brook_linux_arm64",
                "digest": digest}]}

    def test_release_selection(self):
        metadata = self.metadata("sha256:" + "a" * 64)
        self.assertEqual(m.release_asset(metadata, "arm64")[2], "a" * 64)
        for change in ({"prerelease": True}, {"tag_name": "../../bad"}):
            with self.assertRaises(ValueError):
                m.release_asset(dict(metadata, **change), "arm64")
        with self.assertRaises(ValueError):
            m.release_asset(metadata, "amd64")
        with self.assertRaises(ValueError):
            m.release_asset(metadata, "arm64", "v20260101")

    def test_no_silent_checksum_downgrade(self):
        with self.assertRaises(ValueError):
            m.release_asset(self.metadata(), "arm64")
        self.assertEqual(m.release_asset(self.metadata(), "arm64", checksum="b" * 64)[2], "b" * 64)
        with self.assertRaises(ValueError):
            m.release_asset(self.metadata("sha256:" + "a" * 64), "arm64", checksum="b" * 64)

    def test_digest_verified_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "binary"
            path.write_bytes(b"\x7fELFfake")
            with mock.patch.object(m, "run") as execute, self.assertRaises(ValueError):
                m.verify_binary(path, "0" * 64)
            execute.assert_not_called()
            path.write_bytes(b"<html>bad download</html>")
            with mock.patch.object(m, "run") as execute, self.assertRaises(ValueError):
                m.verify_binary(path, hashlib.sha256(path.read_bytes()).hexdigest())
            execute.assert_not_called()


class TransactionTests(unittest.TestCase):
    def test_failed_config_restart_restores_exact_bytes_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, unit = root / "brook.conf", root / "service"
            original = b"# keep this comment\r\n80 example.com 443 1\r\n"
            config.write_bytes(original)
            unit.write_text("# Managed by brook-pf v2")
            calls = []

            def systemctl(*args, **kwargs):
                calls.append(args)
                if args[0] == "restart":
                    raise RuntimeError("port busy")
                return subprocess.CompletedProcess(args, 0)

            with mock.patch.multiple(m, CONF=config, UNIT=unit, BACKUPS=root / "backups"), \
                    mock.patch.object(m, "systemctl", side_effect=systemctl), \
                    mock.patch.object(m, "active", return_value=True), \
                    mock.patch.object(m, "enabled", return_value=True), \
                    mock.patch.object(m, "legacy_pids", return_value=[]):
                with self.assertRaises(RuntimeError):
                    m.save_rules(m.parse_config("80 other.example 80 1"))
            self.assertEqual(config.read_bytes(), original)
            self.assertIn(("start", m.SERVICE), calls)
            self.assertIn(("enable", m.SERVICE), calls)
            self.assertEqual(len(list((root / "backups").glob("*/manifest.json"))), 1)

    def test_stopped_service_stays_stopped_on_config_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "brook.conf"
            config.write_text("")
            with mock.patch.multiple(m, CONF=config, BACKUPS=root / "backups"), \
                    mock.patch.object(m, "systemctl") as ctl, \
                    mock.patch.object(m, "active", return_value=False), \
                    mock.patch.object(m, "enabled", return_value=False), \
                    mock.patch.object(m, "legacy_pids", return_value=[]):
                m.save_rules(m.parse_config("80 example.com 80 1"))
            ctl.assert_not_called()
            self.assertEqual(m.read_rules(config)[0][0], 80)

    def test_validation_precedes_backups_and_mutation(self):
        with mock.patch.object(m, "Transaction") as transaction, self.assertRaises(ValueError):
            m.save_rules([(80, "x", 80, 1, "0.0.0.0"), (80, "y", 80, 1, "0.0.0.0")])
        transaction.assert_not_called()


class BuildTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "standalone launcher targets Linux; exercised in CI/containers")
    def test_non_utf8_console(self):
        # Exercise the shipped launcher, not merely an imported helper. The old
        # script failed at its first Chinese print and escaped stderr as \\uXXXX.
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "brook.conf"
            config.write_text("# 中文注释\n10000 example.com 443 1\n", encoding="utf-8")
            for encoding in ("latin-1", "ascii"):
                env = dict(os.environ, LANG="C", LC_ALL="C", PYTHONUTF8="0",
                           PYTHONCOERCECLOCALE="0", PYTHONIOENCODING=encoding)
                cases = [(["check", str(config)], b"", 0, "配置有效"),
                         (["menu"], b"99\n", 0, "请输入数字 [0-10]"),
                         (["check", str(config) + ".missing"], b"", 1, "错误：")]
                for args, stdin, status, message in cases:
                    with self.subTest(encoding=encoding, args=args):
                        result = subprocess.run(["bash", str(ROOT / "brook-pf.sh")] + args,
                                                env=env, input=stdin, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE)
                        output = (result.stdout + result.stderr).decode("utf-8")
                        self.assertEqual(result.returncode, status, output)
                        self.assertIn(message, output)
                        self.assertNotIn("codec can't encode", output)

    def test_generated_script_is_current(self):
        spec = importlib.util.spec_from_file_location("build", ROOT / "scripts/build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual((ROOT / "brook-pf.sh").read_text(), build.render())

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(ROOT / "brook-pf.sh")], check=True)


if __name__ == "__main__":
    unittest.main()
