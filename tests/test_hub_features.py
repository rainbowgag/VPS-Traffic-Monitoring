"""阶段 7+ 新功能测试：分组、小时聚合、CSV、离线告警。"""
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub  # noqa: E402


def make_payload(token, start_ts, rx, tx, ts=1700000000):
    return {
        "token": token,
        "hostname": "vps1",
        "ts": ts,
        "tz_offset_minutes": 480,
        "reset_day": 1,
        "cycle": {"start_ts": start_ts, "rx_bytes": rx, "tx_bytes": tx},
        "interfaces": [{"name": "eth0", "rx_bytes": rx, "tx_bytes": tx}],
        "rates": {"rx_bps": 1, "tx_bps": 2},
        "metrics": {"cpu_percent": 12.3, "mem_used_bytes": 1024, "mem_total_bytes": 4096, "load1": 0.5},
    }


class TestHubFeatures(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"hub_feat_{uuid.uuid4().hex}.db")
        self.store = hub.HubStore(self.db_path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_group_field(self):
        token = self.store.add_node("vps1", group="香港")
        node = self.store.get_node_by_token(token)
        self.assertEqual(node["group"], "香港")
        self.store.update_node(node["id"], group="日本")
        self.assertEqual(self.store.get_node(node["id"])["group"], "日本")

    def test_hourly_usage_and_csv(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        self.store.ingest_report(make_payload(token, 1000, 130, 250))
        hours = self.store.hourly_usage(1, 24)
        self.assertEqual(len(hours), 1)
        self.assertEqual(hours[0]["hour"], "2023-11-15 06:00")
        self.assertEqual(hours[0]["total_bytes"], 80)
        csv_text = self.store.csv_export(1, 7)
        self.assertIn("hour,rx_bytes,tx_bytes,total_bytes", csv_text)
        self.assertIn("2023-11-15 06:00,30,50,80", csv_text)

    def test_offline_alert_and_recovery(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        original = hub.utc_now
        base = datetime(2023, 11, 15, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()
        try:
            hub.utc_now = lambda: int(base)
            self.store.evaluate_online()  # online, no alert
            self.assertIsNone(self.store.latest_alert(1))
            hub.utc_now = lambda: int(base + 600)
            self.store.evaluate_online()  # offline
            latest = self.store.latest_alert(1)
            self.assertEqual(latest["status"], "offline")
            hub.utc_now = lambda: int(base)
            self.store.evaluate_online()  # recovered
            self.assertEqual(self.store.latest_alert(1)["status"], "recovered")
        finally:
            hub.utc_now = original


if __name__ == "__main__":
    unittest.main()
