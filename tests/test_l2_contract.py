import importlib
import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "starter_kit" / "llm_client.py"
POLICY = ROOT / "starter_kit" / "l2_policy.json"


def load_client():
    spec = importlib.util.spec_from_file_location("loomq_public_llm_client", CLIENT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CompatibleAPIHandler(BaseHTTPRequestHandler):
    request_payload = None

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        type(self).request_payload = json.loads(self.rfile.read(length))
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PublicL2ContractTests(unittest.TestCase):
    def test_adapter_supports_standard_package_import(self):
        adapter = importlib.import_module("starter_kit.adapter")

        self.assertEqual(adapter.SUPPORTED_TARGETS, ("spinq", "originq", "braket"))

    def test_policy_is_the_published_formal_deepseek_budget(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(policy["formal_model"], "deepseek-v4-flash")
        self.assertEqual(policy["thinking"], {"type": "disabled"})
        self.assertEqual(
            policy["per_case"],
            {"timeout_seconds": 120},
        )
        self.assertFalse(policy["organizer_api_available_before_scoring"])

    def test_missing_environment_fails_without_echoing_secrets(self):
        client = load_client()
        with mock.patch.dict(os.environ, {"UNRELATED_SECRET": "do-not-echo"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "LOOMQ_LLM_BASE_URL") as caught:
                client.chat_completion([{"role": "user", "content": "hello"}])
        self.assertNotIn("do-not-echo", str(caught.exception))

    def test_client_works_with_an_openai_compatible_endpoint(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CompatibleAPIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            environment = {
                "LOOMQ_LLM_BASE_URL": "http://127.0.0.1:%d" % server.server_port,
                "LOOMQ_LLM_API_KEY": "local-key",
                "LOOMQ_LLM_MODEL": "local-model",
                "LOOMQ_LLM_TIMEOUT_SECONDS": "2",
                }
            with mock.patch.dict(os.environ, environment, clear=True):
                response = load_client().chat_completion([{"role": "user", "content": "hello"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(response["choices"][0]["message"]["content"], "ok")
        self.assertEqual(CompatibleAPIHandler.request_payload["model"], "local-model")
        self.assertEqual(CompatibleAPIHandler.request_payload["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
