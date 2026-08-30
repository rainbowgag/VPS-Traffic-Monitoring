"""项目骨架冒烟测试。只验证可导入与最小配置，不访问网络。/proc/net/dev。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub  # noqa: E402
import monitor  # noqa: E402


class TestProjectSmoke(unittest.TestCase):
    def test_monitor_imports_and_name(self):
        self.assertEqual(monitor.APP_NAME, "vps-traffic-monitor")

    def test_hub_imports_and_name(self):
        self.assertEqual(hub.APP_NAME, "vps-traffic-hub")

    def test_hub_load_config_defaults(self):
        path = os.path.join(tempfile.gettempdir(), "vps_traffic_hub_test_config.json")
        config = hub.load_config(path)
        self.assertEqual(config["port"], hub.DEFAULT_PORT)
        self.assertIsInstance(config["smtp"], dict)
        self.assertTrue(config["secret_key"])

    def test_hub_load_config_merges_existing(self):
        path = os.path.join(tempfile.gettempdir(), "vps_traffic_hub_test_config_merge.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"port": 9999, "alert_cooldown_hours": 6}')
        try:
            config = hub.load_config(path)
            self.assertEqual(config["port"], 9999)
            self.assertEqual(config["alert_cooldown_hours"], 6)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
