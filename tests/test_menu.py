import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from test_manager import m, ROOT


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.root / "brook"
        self.binary.touch()
        self.config = self.root / "brook.conf"
        self.config.write_text("10000 example.com 443 1 ::1\n10001 example.net 80 0 0.0.0.0\n")
        patches = [mock.patch.multiple(m, BIN=self.binary, CONF=self.config),
                   mock.patch.object(m, "active", return_value=True),
                   mock.patch.object(m, "legacy_pids", return_value=[]),
                   mock.patch.object(m, "server_ip", return_value="203.0.113.1"),
                   mock.patch.object(m.shutil, "which", return_value="/bin/systemctl")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def invoke(self, answers, side_effect=None):
        stream = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers) as prompt, \
                mock.patch.object(m, "main", side_effect=side_effect) as dispatch, \
                contextlib.redirect_stdout(stream):
            m.menu()
        return plain(stream.getvalue()), prompt, dispatch

    def test_main_menu_matches_original_layout_and_numbers(self):
        output, prompt, dispatch = self.invoke(["99"])
        expected = """
  Brook 端口转发 一键管理脚本 [v{version}]
  ---- Toyo | doub.io/wlzy-jc37 ----
  
  0. 升级脚本
————————————
  1. 安装 Brook
  2. 更新 Brook
  3. 卸载 Brook
————————————
  4. 启动 Brook
  5. 停止 Brook
  6. 重启 Brook
————————————
  7. 设置 Brook 端口转发
  8. 查看 Brook 端口转发
  9. 查看 Brook 日志
 10. 监控 Brook 运行状态
————————————

 当前状态: 已安装 并 已启动

请输入正确数字 [0-10]
""".format(version=m.VERSION)
        self.assertEqual(output, expected)
        prompt.assert_called_once_with(" 请输入数字 [0-10]:")
        dispatch.assert_not_called()

    def test_zero_updates_script_instead_of_exiting(self):
        _, _, dispatch = self.invoke(["0"])
        dispatch.assert_called_once_with(["self-update"])

    def test_service_menu_routes(self):
        for choice, command in (("4", "start"), ("5", "stop"), ("6", "restart"), ("9", "logs")):
            with self.subTest(choice=choice):
                _, _, dispatch = self.invoke([choice])
                dispatch.assert_called_once_with([command])

    def test_status_missing_and_stopped(self):
        with mock.patch.object(m, "active", return_value=False):
            output, _, _ = self.invoke(["99"])
            self.assertIn("当前状态: 已安装 但 未启动", output)
        self.binary.unlink()
        output, _, _ = self.invoke(["99"])
        self.assertIn("当前状态: 未安装", output)

    def test_view_uses_original_labels_and_states(self):
        output, _, dispatch = self.invoke(["8"])
        self.assertIn("当前端口转发总数:  2  当前服务器IP:  203.0.113.1 ", output)
        self.assertIn("本地监听端口: 10000\t 被转发IP: example.com\t 被转发端口: 443\t 状态: 启用", output)
        self.assertIn("状态: 禁用", output)
        dispatch.assert_not_called()

    def test_add_prompts_and_continue(self):
        output, prompt, dispatch = self.invoke(["7", "1", "10002", "new.example", "8443", "", "n"])
        dispatch.assert_called_once_with(["add", "10002", "new.example", "8443"])
        self.assertIn("4.  启用/禁用 端口转发", output)
        self.assertNotIn("5. 禁用", output)
        self.assertNotIn("监听 IP", output)
        self.assertIn("请输入被转发的 IP :", output)
        self.assertEqual(prompt.call_args_list[-1], mock.call("是否继续 添加端口转发配置？[Y/n]:"))

    def test_add_can_repeat_and_disable(self):
        _, _, dispatch = self.invoke(["7", "1", "10002", "new.example", "443", "n", "",
                                       "10003", "other.example", "80", "", "n"])
        self.assertEqual(dispatch.call_args_list, [mock.call(["add", "10002", "new.example", "443", "--disabled"]),
                                                  mock.call(["add", "10003", "other.example", "80"])])

    def test_modify_keeps_ipv6_listener_and_original_prompt_order(self):
        output, _, dispatch = self.invoke(["7", "3", "10000", "10005", "new.example", "8443", "n"])
        dispatch.assert_called_once_with(["set", "10000", "new.example", "8443", "--new-port", "10005",
                                          "--listen", "::1", "--disabled"])
        self.assertIn("开始输入新端口...", output)

    def test_toggle_combined_menu_both_directions_and_cancel(self):
        for number, command in (("10000", "disable"), ("10001", "enable")):
            _, _, dispatch = self.invoke(["7", "4", number, ""])
            dispatch.assert_called_once_with([command, number])
        output, _, dispatch = self.invoke(["7", "4", "10000", "n"])
        dispatch.assert_not_called()
        self.assertIn("已取消...", output)

    def test_delete_continues_and_empty_input_cancels(self):
        _, prompt, dispatch = self.invoke(["7", "2", "10000", "n"])
        dispatch.assert_called_once_with(["delete", "10000"])
        self.assertEqual(prompt.call_args_list[-1], mock.call("是否继续 删除端口转发配置？[Y/n]:"))
        for answers in (["7", ""], ["7", "1", ""], ["7", "1", "oops", "0", ""]):
            output, _, dispatch = self.invoke(answers)
            dispatch.assert_not_called()
            self.assertIn("已取消...", output)

    def test_monitor_original_defaults_and_meaning(self):
        with mock.patch.object(m, "monitor_enabled", return_value=True):
            output, _, dispatch = self.invoke(["10", ""])
            self.assertIn("当前监控模式: 已开启", output)
            dispatch.assert_not_called()
            _, _, dispatch = self.invoke(["10", "y"])
            dispatch.assert_called_once_with(["monitor-off"])
        with mock.patch.object(m, "monitor_enabled", return_value=False):
            _, _, dispatch = self.invoke(["10", ""])
            dispatch.assert_called_once_with(["monitor-on"])

    def test_uninstall_requires_original_confirmation(self):
        _, _, dispatch = self.invoke(["3", ""])
        dispatch.assert_not_called()
        _, _, dispatch = self.invoke(["3", "y"])
        dispatch.assert_called_once_with(["uninstall", "--yes"])


class SelfUpdateTests(unittest.TestCase):
    def test_script_update_verifies_asset_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, installed = directory / "downloaded.sh", directory / "installed.sh"
            source.write_text("old script")
            installed.write_text("old installed script")
            data = (ROOT / "brook-pf.sh").read_bytes()
            tag = "v" + m.VERSION
            url = "https://github.com/LianZiZhou/brook-pf/releases/download/{}/brook-pf.sh".format(tag)
            metadata = {"tag_name": tag, "assets": [{"name": "brook-pf.sh", "browser_download_url": url,
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest()}]}

            def download(url, path, limit):
                path.write_bytes(json.dumps(metadata).encode() if url.endswith("/latest") else data)

            with mock.patch.multiple(m, SOURCE=source, MANAGER=installed), \
                    mock.patch.object(m, "installed", return_value=True), \
                    mock.patch.object(m, "fetch", side_effect=download), \
                    mock.patch.object(m, "Transaction") as transaction:
                metadata["assets"][0]["digest"] = "sha256:" + "0" * 64
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    m.self_update()
                transaction.assert_not_called()
                self.assertEqual(source.read_text(), "old script")
                metadata["assets"][0]["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
                m.self_update()
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(installed.read_bytes(), data)
            transaction.assert_called_once_with([source, installed])


if __name__ == "__main__":
    unittest.main()
