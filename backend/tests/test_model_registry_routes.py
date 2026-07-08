import asyncio
import unittest

from fastapi import HTTPException

try:
    from backend import server
except ImportError:
    import server


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class ModelRegistryRouteTests(unittest.TestCase):
    def tearDown(self):
        for feature in server.model_registry.FEATURES:
            run(server.db.model_registry.delete_one({"id": feature}))

    def test_get_fills_in_defaults_for_unset_features(self):
        entries = run(server.get_model_registry())
        by_id = {entry["id"]: entry for entry in entries}
        self.assertEqual(set(by_id), set(server.model_registry.FEATURES))
        for entry in entries:
            self.assertIsNone(entry["updated_at"])
            self.assertEqual(entry["provider"], "ollama")

    def test_put_then_get_reflects_update(self):
        run(server.set_model_registry_entry("review", server.RegistryUpdateBody(provider="ollama", model="qwen2.5-coder:1.5b")))

        entries = run(server.get_model_registry())
        by_id = {entry["id"]: entry for entry in entries}
        self.assertEqual(by_id["review"]["model"], "qwen2.5-coder:1.5b")
        self.assertIsNotNone(by_id["review"]["updated_at"])
        # Other features remain default-filled.
        self.assertIsNone(by_id["search"]["updated_at"])

    def test_put_twice_updates_in_place(self):
        run(server.set_model_registry_entry("review", server.RegistryUpdateBody(provider="ollama", model="model-a")))
        run(server.set_model_registry_entry("review", server.RegistryUpdateBody(provider="ollama", model="model-b")))

        provider, model = run(server.get_active_model("review"))
        self.assertEqual(model, "model-b")

    def test_invalid_feature_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.set_model_registry_entry("not-a-feature", server.RegistryUpdateBody(provider="ollama", model="x")))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_get_active_model_falls_back_to_default_when_unset(self):
        provider, model = run(server.get_active_model("ask"))
        self.assertEqual(provider, "ollama")
        self.assertTrue(model)


if __name__ == "__main__":
    unittest.main()
