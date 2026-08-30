"""HubStore 阶段 1 单元测试：token、增量计算、周期重置、计数器回退。"""
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub  # noqa: E402


def make_payload(token, start_ts, rx, tx, hostname="vps1", reset_day=1, tz=480):
    return {
        "token": token,
        "hostname": hostname,
        "ts": 1700000000,
        "tz_offset_minutes": tz,
        "reset_day": reset_day,
        "cycle": {"start_ts": start_ts, "rx_bytes": rx, "tx_bytes": tx},
        "interfaces": [{"name": "eth0", "rx_bytes": rx, "tx_bytes": tx}],
        "rates": {"rx_bps": 1, "tx_bps": 2},
    }


class TestHubStore(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"hub_test_{uuid.uuid4().hex}.db")
        self.store = hub.HubStore(self.db_path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_add_and_verify_token(self):
        token = self.store.add_node("vps1")
        self.assertTrue(self.store.get_node_by_token(token))
        self.assertIsNone(self.store.get_node_by_token("wrong-token"))

    def test_first_report_sets_baseline_with_zero_delta(self):
        token = self.store.add_node("vps1")
        result = self.store.ingest_report(make_payload(token, 1000, 100, 200))
        self.assertEqual(result["delta_rx_bytes"], 0)
        self.assertEqual(result["delta_tx_bytes"], 0)
        node = self.store.get_node_by_token(token)
        self.assertEqual(node["last_rx_bytes"], 100)
        self.assertEqual(node["last_tx_bytes"], 200)
        sample = self.store.last_sample(node["id"])
        self.assertEqual(sample["delta_rx_bytes"], 0)

    def test_second_report_computes_delta(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        result = self.store.ingest_report(make_payload(token, 1000, 130, 250))
        self.assertEqual(result["delta_rx_bytes"], 30)
        self.assertEqual(result["delta_tx_bytes"], 50)

    def test_cycle_reset_resets_baseline(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        # 新周期：即使累计值更小，也视为新基线，增量应为 0
        result = self.store.ingest_report(make_payload(token, 2000, 10, 20))
        self.assertEqual(result["delta_rx_bytes"], 0)
        self.assertEqual(result["delta_tx_bytes"], 0)
        node = self.store.get_node_by_token(token)
        self.assertEqual(node["last_cycle_start_ts"], 2000)
        self.assertEqual(node["last_rx_bytes"], 10)

    def test_counter_rollback_gives_zero_delta(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        result = self.store.ingest_report(make_payload(token, 1000, 90, 150))
        self.assertEqual(result["delta_rx_bytes"], 0)
        self.assertEqual(result["delta_tx_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
