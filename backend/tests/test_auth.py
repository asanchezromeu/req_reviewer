import os
import unittest

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

try:
    from backend.auth import require_api_key
except ImportError:
    from auth import require_api_key


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected():
        return {"ok": True}

    return app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self._original = os.environ.get("API_KEYS")

    def tearDown(self):
        if self._original is None:
            os.environ.pop("API_KEYS", None)
        else:
            os.environ["API_KEYS"] = self._original

    def test_unauthenticated_when_no_keys_configured(self):
        os.environ.pop("API_KEYS", None)
        client = TestClient(_build_app())

        response = client.get("/protected")

        self.assertEqual(response.status_code, 200)

    def test_rejects_missing_bearer_token_when_keys_configured(self):
        os.environ["API_KEYS"] = "secret-key"
        client = TestClient(_build_app())

        response = client.get("/protected")

        self.assertEqual(response.status_code, 401)

    def test_rejects_wrong_bearer_token(self):
        os.environ["API_KEYS"] = "secret-key"
        client = TestClient(_build_app())

        response = client.get("/protected", headers={"Authorization": "Bearer wrong-key"})

        self.assertEqual(response.status_code, 401)

    def test_accepts_correct_bearer_token(self):
        os.environ["API_KEYS"] = "secret-key,other-key"
        client = TestClient(_build_app())

        response = client.get("/protected", headers={"Authorization": "Bearer other-key"})

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
