"""Tests for the discover layer that joins provider metadata + .api_keys/.

The ``swe providers`` commands built on it are tested in
test_providers_commands.py.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from swe.providers.defaults import DEFAULT_PROVIDERS
from swe.providers.discover import discover_provider_keys


class DiscoverProviderKeysTest(unittest.TestCase):
    def test_returns_one_row_per_provider(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.mkdir()
            rows = discover_provider_keys(dict(DEFAULT_PROVIDERS), keys_dir)
            self.assertEqual(len(rows), len(DEFAULT_PROVIDERS))

    def test_present_key_is_masked(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.mkdir()
            (keys_dir / "openai").write_text(
                "sk-proj-AbCdEfGh1234567890" * 2
            )

            rows = discover_provider_keys(
                {"openai": DEFAULT_PROVIDERS["openai"]}, keys_dir
            )
            openai_row = next(r for r in rows if r["name"] == "openai")
            self.assertNotEqual(openai_row["masked"], "-")
            self.assertIn("***", openai_row["masked"])
            self.assertTrue(openai_row["raw_key"].startswith("sk-proj-"))
            self.assertTrue(openai_row["raw_key"].endswith("7890"))
            self.assertEqual(openai_row["key_file"], str(keys_dir / "openai"))

    def test_missing_key_shows_dash(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.mkdir()
            rows = discover_provider_keys(
                {"anthropic": DEFAULT_PROVIDERS["anthropic"]}, keys_dir
            )
            row = rows[0]
            self.assertEqual(row["masked"], "-")
            self.assertIsNone(row["raw_key"])

    def test_provider_without_filename_yields_none_key_file(self):
        providers = {"myprov": {"name": "myprov", "env_vars": ["MY_KEY"]}}
        with TemporaryDirectory() as tmp:
            keys_dir = Path(tmp) / ".api_keys"
            keys_dir.mkdir()
            rows = discover_provider_keys(providers, keys_dir)
            self.assertIsNone(rows[0]["key_file"])
            self.assertEqual(rows[0]["masked"], "-")

    def test_discover_provider_keys_reads_shell_export_file(self):
        """When keys_dir is a regular file, parse it as shell exports."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.write_text(
                "# AI API Keys\n"
                "\n"
                "# OpenAI\n"
                "export OPENAI_API_KEY=sk-proj-DiscoverKey1234\n"
                "\n"
                "# Anthropic\n"
                "export ANTHROPIC_API_KEY=sk-ant-DiscoverKey1234\n"
                "\n"
                "# Mistral (no key set)\n"
            )
            providers = {
                "openai": DEFAULT_PROVIDERS["openai"],
                "anthropic": DEFAULT_PROVIDERS["anthropic"],
                "mistral": DEFAULT_PROVIDERS["mistral"],
            }
            rows = discover_provider_keys(providers, keys_dir)
            by_name = {r["name"]: r for r in rows}

            self.assertNotEqual(by_name["openai"]["masked"], "-")
            self.assertIn("***", by_name["openai"]["masked"])
            self.assertEqual(
                by_name["openai"]["raw_key"], "sk-proj-DiscoverKey1234"
            )
            self.assertEqual(by_name["openai"]["key_file"], str(keys_dir))
            self.assertEqual(by_name["openai"]["matched_env"], "OPENAI_API_KEY")

            self.assertNotEqual(by_name["anthropic"]["masked"], "-")
            self.assertEqual(
                by_name["anthropic"]["raw_key"], "sk-ant-DiscoverKey1234"
            )
            self.assertEqual(by_name["anthropic"]["matched_env"], "ANTHROPIC_API_KEY")

            # Mistral has env var declared but no matching export in the file.
            self.assertEqual(by_name["mistral"]["masked"], "-")
            self.assertIsNone(by_name["mistral"]["raw_key"])
            self.assertIsNone(by_name["mistral"]["matched_env"])


if __name__ == "__main__":
    unittest.main()
