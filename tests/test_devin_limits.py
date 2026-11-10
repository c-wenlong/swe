"""Free-model limits are observations, never inferred quota percentages."""

import datetime as dt
import gzip
import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from swe.harness.devin_limits import free_model_reset
from swe.harness.models import RateLimitInfo
from tests._tempdir import temp_dir


def epoch(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class DevinFreeLimitsTest(unittest.TestCase):
    def setUp(self):
        # Parsing tests drive time explicitly; machine load must not
        # consume their optional scan budget.
        self.enterContext(patch("swe.harness.devin_limits.time.monotonic", return_value=0.0))
        self.root = temp_dir(self)
        self.credentials = self.root / "devin/credentials.toml"
        self.logs = self.credentials.parent / "cli/logs"
        self.logs.mkdir(parents=True)
        self.now = epoch("2026-11-09T14:05:00Z")

    def error(self, seen="2026-11-09T14:04:41Z", reset="19:01"):
        return (f"{seen}  WARN affogato::agent::control_loop: "
                "error=Inference(ServerError(message=Reached free model rate limit. "
                "Please switch to a different model. Your limit will reset in "
                f"4 hours 55 minutes (at {reset} UTC).))\n")

    def write(self, name, text):
        path = self.logs / name
        if name.endswith(".gz"):
            with gzip.open(path, "wt") as file:
                file.write(text)
        else:
            path.write_text(text)
        os.utime(path, (self.now, self.now))
        return path

    def read(self, now=None):
        return free_model_reset(str(self.credentials), now=self.now if now is None else now)

    def test_observed_block_and_expiry(self):
        self.write("devin_test.log", self.error())
        self.assertEqual(self.read(), epoch("2026-11-09T19:01:00Z"))
        self.assertEqual(self.read(epoch("2026-11-09T19:01:00Z")), 0)

    def test_midnight_is_utc_not_local(self):
        self.write("devin_test.log", self.error("2026-11-09T23:30:00Z", "01:05"))
        self.assertEqual(self.read(epoch("2026-11-09T23:31:00Z")),
                         epoch("2026-11-10T01:05:00Z"))

    def test_reset_in_logged_minute_does_not_add_a_day(self):
        self.write("devin_test.log", self.error(reset="14:04"))
        self.assertEqual(self.read(), 0)

    def test_compressed_log_and_corrupt_neighbor(self):
        self.write("devin_old.log.gz", self.error())
        (self.logs / "devin_bad.log.gz").write_bytes(b"not gzip")
        self.assertEqual(self.read(), epoch("2026-11-09T19:01:00Z"))

    def test_latest_event_wins_even_when_file_order_differs(self):
        self.write("devin_new.log", self.error())
        old = self.write("devin_old.log.gz", self.error("2026-11-09T13:00:00Z", "20:00"))
        os.utime(old, (self.now + 1, self.now + 1))
        self.assertEqual(self.read(), epoch("2026-11-09T19:01:00Z"))

    def test_no_error_is_unknown_and_other_accounts_are_isolated(self):
        self.assertEqual(self.read(), 0)
        self.write("devin_test.log", self.error())
        self.assertEqual(free_model_reset(str(self.root / "other/credentials.toml"), now=self.now), 0)

    def test_replaced_credentials_do_not_inherit_old_blocks(self):
        self.write("devin_test.log", self.error())
        self.credentials.write_text('windsurf_api_key="different-account"\n')
        os.utime(self.credentials, (self.now, self.now))
        self.assertEqual(self.read(), 0)

    def test_malformed_future_and_stale_errors_are_ignored(self):
        self.write("devin_test.log", "".join([
            self.error(reset="99:99"), self.error("2026-11-10T14:04:41Z"),
            self.error("2026-11-01T14:04:41Z"),
            self.error().replace("WARN affogato", "INFO affogato"),
            "quoted prompt: " + self.error(),
        ]))
        self.assertEqual(self.read(), 0)

    def test_reads_tail_of_large_log(self):
        self.write("devin_large.log", "x" * (3 * 1024 * 1024) + "\n" + self.error())
        self.assertGreater(self.read(), self.now)

    def test_old_files_and_unrelated_logs_are_skipped(self):
        old = self.write("devin_old.log", self.error())
        os.utime(old, (self.now - 8 * 86400, self.now - 8 * 86400))
        self.write("other.log", self.error())
        self.assertEqual(self.read(), 0)

    def test_aggregate_byte_budget_bounds_multiple_gzip_logs(self):
        for i in range(3):
            self.write(f"devin_{i}.log.gz", "padding\n" * 100)
        opened = []
        sizes = []
        original_open = gzip.open

        @contextmanager
        def measured_open(path, mode):
            opened.append(path)
            with original_open(path, mode) as stream:
                class Reader:
                    def read(self, count):
                        chunk = stream.read(count)
                        sizes.append(len(chunk))
                        return chunk
                yield Reader()

        with patch("swe.harness.devin_limits._SCAN_MAX_BYTES", 1000), \
             patch("swe.harness.devin_limits._READ_CHUNK_BYTES", 64), \
             patch("swe.harness.devin_limits.gzip.open", side_effect=measured_open):
            self.assertEqual(self.read(), 0)
        self.assertEqual(len(opened), 2)
        self.assertEqual(sum(sizes), 1000)
        self.assertLessEqual(max(sizes), 64)

    def test_time_budget_stops_inflation_between_chunks(self):
        self.write("devin_large.log.gz", "padding\n" * 100)
        clock = [0.0]
        reads = []
        original_open = gzip.open

        @contextmanager
        def slow_open(path, mode):
            with original_open(path, mode) as stream:
                class Reader:
                    def read(self, count):
                        clock[0] += 0.06
                        reads.append(count)
                        return stream.read(count)
                yield Reader()

        with patch("swe.harness.devin_limits.time.monotonic", side_effect=lambda: clock[0]), \
             patch("swe.harness.devin_limits._READ_CHUNK_BYTES", 64), \
             patch("swe.harness.devin_limits.gzip.open", side_effect=slow_open):
            self.assertEqual(self.read(), 0)
        self.assertEqual(reads, [64, 64])

    def test_time_budget_includes_directory_discovery_and_parsing(self):
        self.write("devin_test.log", self.error())
        with patch("swe.harness.devin_limits.time.monotonic", side_effect=[0, 0, 1]):
            self.assertEqual(self.read(), 0)

        from swe.harness import devin_limits
        clock = [0.0]
        pattern = devin_limits._ERROR

        def slow_parse(line):
            clock[0] = 1.0
            return pattern.search(line)

        # Finding an error on the final line must not publish evidence
        # after the parse budget expires, either.
        with patch("swe.harness.devin_limits.time.monotonic", side_effect=lambda: clock[0]), \
             patch("swe.harness.devin_limits._ERROR") as error:
            error.search.side_effect = slow_parse
            self.assertEqual(self.read(), 0)

    def test_paid_reading_returns_without_scanning_near_caller_deadline(self):
        from swe.harness.rate_limits import _fetch_devin
        from tests.test_rate_limits import DevinFetcherTest

        with patch("swe.harness.rate_limits._read_devin_credentials", return_value=("fake", "https://example.com")), \
             patch("swe.harness.rate_limits._fetch_json", return_value=DevinFetcherTest._SAMPLE_RESPONSE), \
             patch("swe.harness.rate_limits._fetch_cutoff", SimpleNamespace(at=10.1)), \
             patch("swe.harness.devin_limits.time.monotonic", return_value=10.0), \
             patch("swe.harness.devin_limits.Path.iterdir") as scan:
            info = _fetch_devin(str(self.credentials))
        self.assertEqual(info.used_percent, 4)
        self.assertEqual(info.free_model_reset_at, 0)
        scan.assert_not_called()

    def test_fetch_preserves_paid_quota_and_uses_accounts_own_logs(self):
        from swe.harness.rate_limits import _fetch_devin
        from tests.test_rate_limits import DevinFetcherTest

        self.write("devin_test.log", self.error())
        with patch("swe.harness.rate_limits._read_devin_credentials", return_value=("fake", "https://example.com")), \
             patch("swe.harness.rate_limits._fetch_json", return_value=DevinFetcherTest._SAMPLE_RESPONSE), \
             patch("swe.harness.devin_limits.time.time", return_value=self.now):
            info = _fetch_devin(str(self.credentials))
        self.assertEqual(info.used_percent, 4)
        self.assertFalse(info.limit_reached)
        self.assertGreater(info.free_model_reset_at, self.now)

    def test_cache_roundtrip_and_json_expire_without_refetch(self):
        from dataclasses import asdict

        reset = epoch("2026-11-09T19:01:00Z")
        info = RateLimitInfo("devin", 4, False, 0, "teams", 86400, free_model_reset_at=reset)
        restored = RateLimitInfo(**asdict(info))
        with patch("swe.harness.models.time.time", return_value=self.now):
            self.assertEqual(restored.to_json()["free_model_reset_at"], reset)
            self.assertIn("free:blocked", restored.format_column())
        with patch("swe.harness.models.time.time", return_value=reset):
            self.assertIsNone(restored.to_json()["free_model_reset_at"])
            self.assertNotIn("free:blocked", restored.format_column())


if __name__ == "__main__":
    unittest.main()
