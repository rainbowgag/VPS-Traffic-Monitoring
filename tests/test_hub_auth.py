"""Hub 管理员鉴权与节点管理测试。"""
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub  # noqa: E402


class TestHubAuthAndNodes(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"hub_auth_{uuid.uuid4().hex}.db")
        self.store = hub.HubStore(self.db_path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_password_hash_roundtrip(self):
        h = hub.hash_password("secret123")
        self.assertTrue(hub.verify_password("secret123", h))
        self.assertFalse(hub.verify_password("wrong", h))

    def test_session_roundtrip(self):
        config = {"admin_user": "admin", "secret_key": "s" * 64}
        token = hub.make_session(config, "admin")
        self.assertTrue(hub.verify_session(config, token))
        self.assertFalse(hub.verify_session(config, "admin:0:bad"))
        bad = dict(config); bad["admin_user"] = "other"
        self.assertFalse(hub.verify_session(bad, token))

    def test_add_node_stores_plaintext_token(self):
        token = self.store.add_node("vps1")
        node = self.store.get_node_by_token(token)
        self.assertIsNotNone(node)
        self.assertEqual(node["token"], token)

    def test_regenerate_and_delete_node(self):
        token = self.store.add_node("vps1")
        node = self.store.get_node_by_token(token)
        new_token = self.store.regenerate_token(node["id"])
        self.assertIsNone(self.store.get_node_by_token(token))
        self.assertEqual(self.store.get_node_by_token(new_token)["id"], node["id"])
        self.store.delete_node(node["id"])
        self.assertIsNone(self.store.get_node(node["id"]))

    def test_overview_admin_fields(self):
        token = self.store.add_node("vps1", threshold_bytes=123)
        public = self.store.overview(admin=False)
        self.assertNotIn("token", public["nodes"][0])
        self.assertEqual(public["nodes"][0]["threshold_bytes"], 123)
        admin = self.store.overview(admin=True)
        self.assertEqual(admin["nodes"][0]["token"], token)
        self.assertEqual(admin["nodes"][0]["threshold_bytes"], 123)


if __name__ == "__main__":
    unittest.main()
