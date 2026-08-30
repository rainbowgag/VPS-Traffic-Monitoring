"""Agent 上报相关纯逻辑测试（不依赖 /proc/net/dev）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor  # noqa: E402


class TestAgentReport(unittest.TestCase):
    def test_load_config_has_report_defaults(self):
        path = os.path.join(tempfile.gettempdir(), "vps_agent_report_test_config.json")
        config = monitor.load_config(path)
        self.assertEqual(config.get("hub_url"), "")
        self.assertEqual(config.get("agent_token"), "")
        self.assertEqual(config.get("report_interval"), 60)

    def test_build_report_structure(self):
        config = {"agent_token": "tok123", "reset_day": 10}
        snapshot = {
            "now": 1700000000,
            "cycle": {"start_ts": 1699999999, "rx_bytes": 111, "tx_bytes": 222},
            "interfaces": [
                {"name": "eth0", "rx_bytes": 111, "tx_bytes": 222},
                {"name": "manual", "rx_bytes": 0, "tx_bytes": 0},
            ],
            "rate": {"rx_bps": 1, "tx_bps": 2},
        }
        report = monitor.build_report(config, snapshot)
        self.assertEqual(report["token"], "tok123")
        self.assertEqual(report["reset_day"], 10)
        self.assertEqual(report["cycle"]["rx_bytes"], 111)
        self.assertEqual(report["cycle"]["tx_bytes"], 222)
        self.assertEqual(report["interfaces"][0]["name"], "eth0")
        self.assertEqual(report["rates"]["tx_bps"], 2)
        self.assertIsInstance(report["tz_offset_minutes"], int)


if __name__ == "__main__":
    unittest.main()
