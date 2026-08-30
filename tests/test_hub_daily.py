"""Hub daily_usage 聚合与 overview 测试（阶段 3）。"""
import os
import sys
import tempfile
import unittest
import uuid

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
    }


class TestHubDaily(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"hub_daily_{uuid.uuid4().hex}.db")
        self.store = hub.HubStore(self.db_path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_node_local_day_timezone(self):
        # 1700000000 = 2023-11-14T22:13:20Z
        self.assertEqual(hub.node_local_day(1700000000, 0), "2023-11-14")
        self.assertEqual(hub.node_local_day(1700000000, 480), "2023-11-15")

    def test_daily_usage_accumulates(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        self.store.ingest_report(make_payload(token, 1000, 130, 250))
        rows = self.store.daily_usage(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day"], "2023-11-15")
        self.assertEqual(rows[0]["rx_bytes"], 30)
        self.assertEqual(rows[0]["tx_bytes"], 50)
        self.assertEqual(rows[0]["total_bytes"], 80)

    def test_overview_contains_node_summary(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        self.store.ingest_report(make_payload(token, 1000, 130, 250))
        overview = self.store.overview()
        self.assertEqual(overview["nodes"][0]["name"], "vps1")
        self.assertEqual(overview["nodes"][0]["current_total_bytes"], 380)
        self.assertTrue(overview["nodes"][0]["online"])
        self.assertEqual(overview["nodes"][0]["daily"][0]["total_bytes"], 80)
        self.assertEqual(overview["nodes"][0]["interfaces"][0]["name"], "eth0")


if __name__ == "__main__":
    unittest.main()
