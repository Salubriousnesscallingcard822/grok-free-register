import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from token_manager.instance_lock import InstanceLock
from token_manager.models import ManagerConfig
from token_manager.pool import TokenPool
from token_manager.server import _origin_allowed, _resolve_runtime_path


class TokenManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.tokens_dir = self.root / "tokens"
        self.data_dir = self.root / "data"
        self.tokens_dir.mkdir()
        self.config = ManagerConfig(
            data_dir=str(self.data_dir),
            tokens_dir=str(self.tokens_dir),
            proxy_url=None,
            free_units_per_account=3,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_token(self, name, *, subject, access, refresh="refresh", **extra):
        path = self.tokens_dir / f"{name}.json"
        payload = {
            "sub": subject,
            "email": f"{subject}@example.com",
            "access_token": access,
            "refresh_token": refresh,
            **extra,
        }
        path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return path

    def test_auto_reload_detects_existing_file_update(self):
        path = self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)
        self.assertEqual(pool.get("one").access_token, "access-1")

        path.write_text(
            json.dumps(
                {
                    "sub": "one",
                    "email": "one@example.com",
                    "access_token": "access-updated-longer",
                    "refresh_token": "refresh-updated",
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(pool.reload_from_disk(), 1)
        self.assertEqual(pool.get("one").access_token, "access-updated-longer")
        self.assertEqual(pool.get("one").refresh_token, "refresh-updated")

    def test_missing_source_disables_record(self):
        path = self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)
        path.unlink()

        self.assertEqual(pool.reload_from_disk(), 1)
        self.assertFalse(pool.get("one").enabled)
        self.assertEqual(pool.get("one").last_error, "source_file_missing")

    def test_invalid_source_disables_existing_record(self):
        path = self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)

        path.write_text("{}", encoding="utf-8")

        self.assertEqual(pool.reload_from_disk(), 1)
        self.assertFalse(pool.get("one").enabled)
        self.assertEqual(pool.get("one").last_error, "source_credentials_missing")
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            pool.refresh("one")

    def test_source_identity_replacement_disables_old_record(self):
        path = self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)

        path.write_text(
            json.dumps(
                {
                    "sub": "two",
                    "email": "two@example.com",
                    "access_token": "access-2-longer",
                    "refresh_token": "refresh-2-longer",
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(pool.reload_from_disk(), 2)
        self.assertFalse(pool.get("one").enabled)
        self.assertEqual(pool.get("one").last_error, "source_identity_replaced")
        self.assertTrue(pool.get("two").enabled)

    def test_source_cannot_override_credential_endpoints(self):
        path = self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)

        path.write_text(
            json.dumps(
                {
                    "sub": "one",
                    "access_token": "access-evil-longer",
                    "refresh_token": "refresh-evil-longer",
                    "base_url": "https://example.com/v1",
                    "token_endpoint": "https://example.com/oauth/token",
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(pool.reload_from_disk(), 1)
        token = pool.get("one")
        self.assertFalse(token.enabled)
        self.assertEqual(token.last_error, "source_endpoint_rejected")
        self.assertEqual(token.access_token, "access-1")

    def test_round_robin_starts_with_first_record(self):
        self.write_token("a", subject="a", access="access-a")
        self.write_token("b", subject="b", access="access-b")
        pool = TokenPool(self.config)

        self.assertEqual(pool.acquire().id, "a")
        self.assertEqual(pool.acquire().id, "b")
        self.assertEqual(pool.acquire().id, "a")

    def test_success_burns_one_unit_without_usage_payload(self):
        self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)

        pool.mark_result("one", ok=True)

        token = pool.get("one")
        self.assertEqual(token.success_count, 1)
        self.assertEqual(token.free_units_used, 1)
        self.assertEqual(token.free_units_remaining, 2)

    def test_generic_429_cools_down_without_marking_depleted(self):
        self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)

        pool.mark_result(
            "one",
            ok=False,
            status_code=429,
            error="too many requests",
            rate_limit_seconds=30,
        )

        token = pool.get("one")
        self.assertFalse(token.depleted)
        self.assertTrue(token.is_rate_limited())
        with self.assertRaisesRegex(RuntimeError, "cooling down"):
            pool.acquire()

    def test_quota_error_marks_record_depleted(self):
        self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)

        pool.mark_result(
            "one",
            ok=False,
            status_code=429,
            error="resource-exhausted",
        )

        token = pool.get("one")
        self.assertTrue(token.depleted)
        self.assertEqual(token.free_units_remaining, 0)

    def test_balance_summary_exposes_frontend_contract(self):
        self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)
        pool.mark_result("one", ok=True)

        summary = pool.balance_summary()

        self.assertEqual(summary["accounts_total"], 1)
        self.assertEqual(summary["accounts_usable_now"], 1)
        self.assertEqual(summary["success_total"], 1)
        self.assertEqual(summary["failed_total"], 0)
        self.assertEqual(summary["free_units_remaining"], 2)

    def test_state_is_redacted_and_integrity_checked(self):
        self.write_token(
            "one",
            subject="one",
            access="access-secret-value",
            refresh="refresh-secret-value",
        )
        pool = TokenPool(self.config)
        pool.mark_result("one", ok=True)

        state_text = pool.state_path.read_text(encoding="utf-8")
        state = json.loads(state_text)
        self.assertNotIn("access-secret-value", state_text)
        self.assertNotIn("refresh-secret-value", state_text)
        self.assertIn("integrity", state)

        state["tokens"]["one"]["success_count"] = 999
        pool.state_path.write_text(json.dumps(state), encoding="utf-8")
        reloaded = TokenPool(self.config)
        self.assertEqual(reloaded.get("one").success_count, 0)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_windows_master_key_is_dpapi_encrypted(self):
        pool = TokenPool(self.config)

        self.assertEqual(pool.master_path.name, "master-key.dpapi")
        self.assertNotIn(pool.master_key.encode("utf-8"), pool.master_path.read_bytes())
        self.assertFalse(pool.legacy_master_path.exists())

        reloaded = TokenPool(self.config)
        self.assertEqual(reloaded.master_key, pool.master_key)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_windows_legacy_master_key_is_migrated(self):
        self.data_dir.mkdir()
        legacy = self.data_dir / "master-key.txt"
        legacy_key = "gk_master_legacy_migration_value"
        legacy.write_text(legacy_key + "\n", encoding="utf-8")

        pool = TokenPool(self.config)

        self.assertEqual(pool.master_key, legacy_key)
        self.assertFalse(legacy.exists())
        self.assertTrue(pool.master_path.exists())
        self.assertNotIn(legacy_key.encode("utf-8"), pool.master_path.read_bytes())

    def test_refresh_is_single_flight_per_token(self):
        source = self.write_token(
            "one",
            subject="one",
            access="access-old",
            refresh="refresh-old",
        )
        pool = TokenPool(self.config)
        calls = []
        calls_lock = threading.Lock()

        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}

            @staticmethod
            def json():
                return {
                    "access_token": "access-new",
                    "refresh_token": "refresh-new",
                    "expires_in": 3600,
                }

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, _endpoint, *, data):
                with calls_lock:
                    calls.append(data["refresh_token"])
                time.sleep(0.08)
                return Response()

        pool._client = lambda: Client()
        start = threading.Barrier(3)
        results = []
        errors = []

        def run_refresh():
            start.wait()
            try:
                results.append(pool.refresh("one"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run_refresh) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(calls, ["refresh-old"])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.refresh_token == "refresh-new" for item in results))
        source_payload = json.loads(source.read_text(encoding="utf-8"))
        self.assertEqual(source_payload["refresh_token"], "refresh-new")

    def test_frontend_has_timeout_mutex_and_mobile_navigation(self):
        static_dir = Path(__file__).resolve().parents[1] / "token_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        styles = (static_dir / "styles.css").read_text(encoding="utf-8")

        self.assertIn("AbortController", script)
        self.assertIn("actionInFlight", script)
        self.assertIn("refreshResultMessage", script)
        self.assertNotIn(".sidebar { display: none; }", styles)

    def test_browser_origin_is_limited_to_local_dashboard(self):
        self.assertTrue(_origin_allowed(None, "127.0.0.1", 8787))
        self.assertTrue(
            _origin_allowed("http://127.0.0.1:8787", "127.0.0.1", 8787)
        )
        self.assertTrue(
            _origin_allowed("http://localhost:8787", "127.0.0.1", 8787)
        )
        self.assertFalse(
            _origin_allowed("https://example.com", "127.0.0.1", 8787)
        )
        self.assertFalse(
            _origin_allowed("http://127.0.0.1:9999", "127.0.0.1", 8787)
        )
        self.assertFalse(_origin_allowed("null", "127.0.0.1", 8787))

    def test_relative_runtime_path_is_anchored(self):
        anchored = _resolve_runtime_path("portable-data", self.root)
        self.assertEqual(anchored, (self.root / "portable-data").resolve())

    def test_instance_lock_is_scoped_to_data_directory(self):
        first = InstanceLock(self.data_dir)
        duplicate = InstanceLock(self.data_dir)
        other = InstanceLock(self.root / "other-data")
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(duplicate.acquire())
            self.assertTrue(other.acquire())
            first.release()
            self.assertTrue(duplicate.acquire())
        finally:
            duplicate.release()
            other.release()
            first.release()


    def test_models_probe_does_not_deplete(self):
        self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)
        pool.mark_result(
            "one",
            ok=False,
            status_code=403,
            error='{"code":"personal-team-blocked:spending-limit"}',
            endpoint="/v1/models",
        )
        token = pool.get("one")
        self.assertFalse(token.depleted)
        self.assertTrue(token.healthy)
        self.assertEqual(token.free_units_remaining, 3)
        self.assertEqual(pool.acquire().id, "one")

    def test_chat_quota_marks_depleted_and_purge_removes(self):
        path = self.write_token("one", subject="one", access="access-1")
        self.write_token("two", subject="two", access="access-2")
        pool = TokenPool(self.config)
        pool.mark_result(
            "one",
            ok=False,
            status_code=402,
            error='{"code":"personal-team-blocked:spending-limit"}',
            endpoint="/v1/chat/completions",
        )
        self.assertTrue(pool.get("one").depleted)
        result = pool.purge_dead_tokens(delete_files=True)
        self.assertEqual(result["removed_count"], 1)
        self.assertIsNone(pool.get("one"))
        self.assertFalse(path.exists())
        self.assertEqual(pool.acquire().id, "two")

    def test_transient_network_error_keeps_token_callable(self):
        self.write_token("one", subject="one", access="access-1")
        pool = TokenPool(self.config)
        pool.mark_result(
            "one",
            ok=False,
            error="[WinError 10054] remote host closed connection",
            endpoint="/v1/chat/completions",
        )
        token = pool.get("one")
        self.assertFalse(token.depleted)
        self.assertTrue(token.healthy)
        self.assertTrue(token.is_rate_limited())


if __name__ == "__main__":
    unittest.main()
