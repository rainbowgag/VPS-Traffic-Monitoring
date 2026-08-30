"""Hub 邮件逻辑测试（阶段 5）：格式化、跳过、告警邮件、恢复邮件、冷却。"""
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

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


class TestHubEmail(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"hub_email_{uuid.uuid4().hex}.db")
        self.store = hub.HubStore(self.db_path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_format_bytes(self):
        self.assertEqual(hub.format_bytes(0), "0 B")
        self.assertEqual(hub.format_bytes(1024), "1.00 KB")
        self.assertEqual(hub.format_bytes(10 * 1024 ** 3), "10.00 GB")

    def test_send_email_requires_complete_config(self):
        with self.assertRaises(ValueError):
            hub.send_email({"enabled": True}, "subj", "body")

    def test_send_email_skip_when_disabled(self):
        self.store.smtp_config = {"enabled": False}
        self.assertFalse(self.store._send_email("subj", "body"))

    def _build_and_fix_clock(self):
        token = self.store.add_node("vps1")
        self.store.ingest_report(make_payload(token, 1000, 100, 200))
        self.store.ingest_report(make_payload(token, 1000, 130, 250))
        fixed = int(datetime(2023, 11, 15, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        hub.utc_now = lambda: fixed
        return token, fixed

    def test_alert_email_sent_once_with_dedupe(self):
        self._build_and_fix_clock()
        self.store.update_node(1, threshold_bytes=1000)
        with mock.patch.object(self.store, "_send_email", return_value=True) as mail:
            self.store.evaluate_node(1)
            self.store.evaluate_node(1)
        self.assertEqual(mail.call_count, 1)
        self.assertIn("预计超限", mail.call_args[0][0])

    def test_recovery_email(self):
        self._build_and_fix_clock()
        self.store.update_node(1, threshold_bytes=1000)
        with mock.patch.object(self.store, "_send_email", return_value=True) as mail:
            self.store.evaluate_node(1)
            self.assertEqual(mail.call_count, 1)
            # 提高阈值，使状态回到 ok
            self.store.update_node(1, threshold_bytes=10000)
            self.store.evaluate_node(1)
        self.assertEqual(mail.call_count, 2)
        self.assertIn("已恢复", mail.call_args_list[1][0][0])


if __name__ == "__main__":
    unittest.main()

