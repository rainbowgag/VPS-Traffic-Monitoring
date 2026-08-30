"""Hub 阈值预测与告警落库测试（阶段 4）。"""
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
    }


class TestHubPrediction(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"hub_pred_{uuid.uuid4().hex}.db")
        self.store = hub.HubStore(self.db_path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_next_reset_local(self):
        tz = 480
        ts = int(datetime(2026, 8, 30, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        self.assertEqual(hub.next_reset_local(ts, 1, tz).date().isoformat(), "2026-09-01")

        ts2 = int(datetime(2026, 8, 5, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        self.assertEqual(hub.next_reset_local(ts2, 10, tz).date().isoformat(), "2026-08-10")

    def _build_node(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        self.store.ingest_report(make_payload(token, 1000, 130, 250))
        return self.store.get_node_by_token(token)

    def test_compute_prediction_warning(self):
        self._build_node()
        self.store.update_node(1, threshold_bytes=1000)
        fixed = int(datetime(2023, 11, 15, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        original = hub.utc_now
        hub.utc_now = lambda: fixed
        try:
            node = self.store.get_node(1)
            pred = self.store.compute_prediction(node)
        finally:
            hub.utc_now = original
        self.assertEqual(pred["current_total_bytes"], 380)
        self.assertEqual(pred["avg_daily_bytes"], 80)
        self.assertEqual(pred["days_left"], 16)
        self.assertEqual(pred["projected_total_bytes"], 1660)
        self.assertEqual(pred["status"], "warning")
        self.assertEqual(pred["projected_exceed_day"], "2023-11-23")

    def test_evaluate_node_dedupe_and_alert(self):
        self._build_node()
        self.store.update_node(1, threshold_bytes=1000)
        fixed = int(datetime(2023, 11, 15, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        original = hub.utc_now
        hub.utc_now = lambda: fixed
        try:
            self.store.evaluate_node(1)
            self.store.evaluate_node(1)
            alerts = [dict(r) for r in self.store.recent_alerts(10)]
        finally:
            hub.utc_now = original
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["status"], "warning")
        self.assertEqual(alerts[0]["predicted_total_bytes"], 1660)

    def test_exceeded_status(self):
        self._build_node()
        self.store.update_node(1, threshold_bytes=100)  # 当前 380 已超过 100
        node = self.store.get_node(1)
        pred = self.store.compute_prediction(node)
        self.assertEqual(pred["status"], "exceeded")
        self.assertTrue(pred["triggered"])


if __name__ == "__main__":
    unittest.main()
