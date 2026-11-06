"""Unit tests for swe.providers.registry — load/save/resolution."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from swe.providers.defaults import (
    DEFAULT_ALL,
    DEFAULT_INFRA,
    DEFAULT_PROVIDERS,
    derived_alias,
    display_name,
)
from swe.providers.registry import alias_map, load_registry, resolve, save_registry


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("swe.providers.registry.CONFIG_DIR", config_dir),
        patch("swe.providers.registry.PROVIDERS_REGISTRY_FILE", registry_file),
    )


class RegistryAutoMergeTest(unittest.TestCase):
    """When DEFAULT_PROVIDERS gains a new built-in, an existing
    providers.json should pick it up on next load without losing the
    user's existing entries or edits."""

    def test_existing_registry_gains_new_defaults(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)
            registry_file.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI (user-tweaked)",
                            "url": "https://api.openai.com",
                            "key_filename": "openai",
                            "env_vars": ["OPENAI_API_KEY"],
                            "aliases": ["oai"],
                        },
                    },
                    indent=2,
                )
            )

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                providers = load_registry()
                # User-editable fields preserved verbatim.
                self.assertEqual(
                    providers["openai"]["url"], "https://api.openai.com"
                )
                self.assertEqual(providers["openai"]["key_filename"], "openai")
                self.assertEqual(
                    providers["openai"]["env_vars"], ["OPENAI_API_KEY"]
                )
                # `name` and `aliases` are derived from env_vars[0] at
                # hydration time (API_KEY is the source of truth).
                self.assertEqual(providers["openai"]["name"], "Openai")
                self.assertEqual(providers["openai"]["aliases"], ["openai"])
                # Defaults built-ins that weren't in the snapshot are merged in.
                for name in DEFAULT_ALL:
                    self.assertIn(name, providers)

    def test_explicit_remove_is_persisted_across_merges(self):
        """If the user removed a built-in, re-loading doesn't bring it back."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)
            registry_file.write_text(
                json.dumps(
                    {
                        "_removed": ["openai"],
                        "anthropic": {
                            "name": "Anthropic",
                            "url": "https://api.anthropic.com",
                            "key_filename": "anthropic",
                            "env_vars": ["ANTHROPIC_API_KEY"],
                            "aliases": [],
                        },
                    },
                    indent=2,
                )
            )

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                providers = load_registry()
                self.assertNotIn("openai", providers)
                self.assertIn("anthropic", providers)
                # Bookkeeping key never leaks into the user-visible view.
                self.assertNotIn("_removed", providers)
                with_removed = load_registry(include_removed=True)
                self.assertEqual(with_removed["_removed"], ["openai"])


class RegistryLoadSeedingTest(unittest.TestCase):
    def test_load_seeds_with_defaults_on_first_run(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                providers = load_registry()
                self.assertEqual(set(providers), set(DEFAULT_ALL))
                self.assertIn("openai", providers)
                self.assertIn("anthropic", providers)
                self.assertTrue(registry_file.exists())
                self.assertEqual(providers["openai"]["key_filename"], "openai")
                # Inference entries hydrate kind; infra entries carry it.
                self.assertEqual(providers["openai"]["kind"], "inference")
                for name in DEFAULT_INFRA:
                    self.assertEqual(providers[name]["kind"], "infra")

    def test_persisted_registry_loads_without_seeding(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)

            payload = {
                "openai": {
                    "name": "OpenAI",
                    "url": "https://x",
                    "key_filename": "openai",
                    "env_vars": ["OPENAI_API_KEY"],
                    "aliases": ["oai"],
                }
            }
            registry_file.write_text(json.dumps(payload, indent=2))

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                loaded = load_registry()
                # Hydration rewrites `name` and `aliases` from env_vars[0]
                # (the API_KEY is the source of truth); preserved fields are
                # the user-editable ones.
                self.assertIn("openai", loaded)
                self.assertEqual(loaded["openai"]["url"], "https://x")
                self.assertEqual(loaded["openai"]["key_filename"], "openai")
                self.assertEqual(loaded["openai"]["env_vars"], ["OPENAI_API_KEY"])
                self.assertEqual(loaded["openai"]["name"], "Openai")
                self.assertEqual(loaded["openai"]["aliases"], ["openai"])

    def test_corrupt_json_raises(self):
        """Corrupt providers.json raises rather than reading as empty — the
        old lenient path let the seed branch overwrite the broken file."""
        from swe.configuration import CorruptConfigurationError

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)
            registry_file.write_text("{not-json}")

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry()
            # The file is left alone for manual recovery.
            self.assertEqual(registry_file.read_text(), "{not-json}")


class RegistryEntryValidationTest(unittest.TestCase):
    """Malformed entries raise CorruptConfigurationError, not a bare
    TypeError/ValueError from ``dict(info)`` or ``.get()`` mid-merge."""

    def _write(self, tmp_path: Path, payload) -> Path:
        config_dir = tmp_path / ".config" / "swe"
        registry_file = config_dir / "providers.json"
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(json.dumps(payload))
        return registry_file

    def test_string_entry_raises_naming_the_row(self):
        from swe.configuration import CorruptConfigurationError

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(tmp_path, {"openai": "oops"})
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                with self.assertRaises(CorruptConfigurationError) as ctx:
                    load_registry()
            self.assertIn("openai", str(ctx.exception))
            # The file is left alone for manual recovery.
            self.assertIn('"oops"', registry_file.read_text())

    def test_list_entry_raises_even_when_dict_would_parse_it(self):
        # ``dict([["env_vars", ["X"]]])`` happily builds a dict — the check
        # must be isinstance, not a try/except around the conversion.
        from swe.configuration import CorruptConfigurationError

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(tmp_path, {"sneaky": [["env_vars", ["X"]]]})
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry()

    def test_removed_must_be_a_list(self):
        # ``set("kimi")`` was silently accepted as four one-letter removals.
        from swe.configuration import CorruptConfigurationError

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(
                tmp_path,
                {
                    "_removed": "kimi",
                    "openai": {"env_vars": ["OPENAI_API_KEY"]},
                },
            )
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                with self.assertRaises(CorruptConfigurationError) as ctx:
                    load_registry()
            self.assertIn("_removed", str(ctx.exception))

    def test_removed_items_must_be_strings(self):
        # ``[["openai"]]`` passed the bare list check, then ``set(...)``
        # raised an uncaught TypeError on the unhashable item.
        from swe.configuration import CorruptConfigurationError

        for bad in ([["openai"]], ["openai", 1], [None]):
            with self.subTest(bad=bad), TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                registry_file = self._write(
                    tmp_path,
                    {
                        "_removed": bad,
                        "openai": {"env_vars": ["OPENAI_API_KEY"]},
                    },
                )
                p1, p2 = _registry_patches(tmp_path / ".config" / "swe",
                                           registry_file)
                with p1, p2:
                    with self.assertRaises(CorruptConfigurationError) as ctx:
                        load_registry()
                self.assertIn("_removed", str(ctx.exception))


class RegistrySaveRoundtripTest(unittest.TestCase):
    def test_save_then_load_roundtrips(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                payload = {
                    "openai": {
                        "name": "OpenAI",
                        "url": "https://x",
                        "key_filename": "openai",
                        "env_vars": ["OPENAI_API_KEY"],
                        "aliases": ["oai"],
                    },
                    "myprov": {
                        "name": "MyProv",
                        "url": "https://y",
                        "key_filename": "myprov",
                        "env_vars": ["MY_KEY"],
                        "aliases": [],
                    },
                }
                save_registry(payload)
                loaded = load_registry()
                # Loading hydrates display fields from env_vars[0] and merges
                # newly shipped defaults into sparse registries.
                self.assertEqual(loaded["openai"]["url"], "https://x")
                self.assertEqual(loaded["openai"]["key_filename"], "openai")
                self.assertEqual(loaded["openai"]["env_vars"], ["OPENAI_API_KEY"])
                self.assertEqual(loaded["openai"]["name"], "Openai")
                self.assertEqual(loaded["openai"]["aliases"], ["openai"])
                self.assertEqual(loaded["myprov"]["url"], "https://y")
                self.assertEqual(loaded["myprov"]["key_filename"], "myprov")
                self.assertEqual(loaded["myprov"]["env_vars"], ["MY_KEY"])
                self.assertEqual(loaded["myprov"]["name"], "My Key")
                self.assertEqual(loaded["myprov"]["aliases"], ["my-key"])
                for name in DEFAULT_ALL:
                    self.assertIn(name, loaded)


class ResolveTest(unittest.TestCase):
    def test_resolve_via_canonical_name(self):
        providers = {
            "openai": {"name": "OpenAI", "aliases": ["oai"]},
        }
        self.assertEqual(resolve(providers, "openai"), "openai")

    def test_resolve_via_alias(self):
        providers = {
            "openai": {"name": "OpenAI", "aliases": ["oai", "gpt"]},
            "anthropic": {"name": "Anthropic", "aliases": ["claude-key"]},
        }
        self.assertEqual(resolve(providers, "oai"), "openai")
        self.assertEqual(resolve(providers, "gpt"), "openai")
        self.assertEqual(resolve(providers, "claude-key"), "anthropic")

    def test_unknown_returns_none(self):
        self.assertIsNone(resolve({}, "missing"))
        providers = {"openai": {"name": "OpenAI"}}
        self.assertIsNone(resolve(providers, "missing"))

    def test_alias_map_contains_all(self):
        providers = {
            "openai": {"name": "OpenAI", "aliases": ["oai", "gpt"]},
        }
        m = alias_map(providers)
        self.assertEqual(set(m), {"openai", "oai", "gpt"})
        for v in m.values():
            self.assertEqual(v, "openai")


class DerivedNamingTest(unittest.TestCase):
    """``name`` and the alias come from ``env_vars[0]`` alone."""

    def test_display_name_strips_the_suffix_and_keeps_ai_capitalised(self):
        cases = {
            "TOGETHER_AI_API_KEY": "Together AI",
            "AI_GATEWAY_TOKEN": "AI Gateway",
            "HUGGINGFACE_HUB_TOKEN": "Huggingface",
            "GITHUB_TOKEN": "Github",
            "PLAIN": "Plain",
        }
        for env_var, name in cases.items():
            with self.subTest(env_var):
                self.assertEqual(display_name({"env_vars": [env_var]}), name)

    def test_display_name_leaves_words_that_start_with_ai_alone(self):
        # Regression: a substring replace gave "My AIrtable".
        self.assertEqual(display_name({"env_vars": ["MY_AIRTABLE_API_KEY"]}),
                         "My Airtable")
        self.assertEqual(display_name({"env_vars": ["OPEN_AIDER_TOKEN"]}),
                         "Open Aider")

    def test_no_env_var_gives_no_name_and_no_alias(self):
        for info in ({}, {"env_vars": []}, {"env_vars": None}):
            with self.subTest(info=info):
                self.assertEqual(display_name(info), "")
                self.assertIsNone(derived_alias(info))

    def test_alias_is_the_lowercased_prefix(self):
        self.assertEqual(derived_alias({"env_vars": ["TOGETHER_AI_API_KEY"]}),
                         "together-ai")
        # _HUB_TOKEN is stripped whole, before _TOKEN could match.
        self.assertEqual(derived_alias({"env_vars": ["HUGGINGFACE_HUB_TOKEN"]}),
                         "huggingface")

    def test_a_bare_suffix_gives_no_alias(self):
        # Nothing is left once the suffix goes, so there is nothing to alias.
        self.assertIsNone(derived_alias({"env_vars": ["_API_KEY"]}))
        self.assertEqual(display_name({"env_vars": ["_API_KEY"]}), "")

    def test_token_compound_suffixes_strip_whole(self):
        # The infra seeds' variable spellings derive clean names:
        # _ACCESS_TOKEN, _AUTH_TOKEN and _API_TOKEN go before _TOKEN.
        cases = {
            "NETLIFY_AUTH_TOKEN": ("Netlify", "netlify"),
            "SUPABASE_ACCESS_TOKEN": ("Supabase", "supabase"),
            "CLOUDFLARE_API_TOKEN": ("Cloudflare", "cloudflare"),
            "VERCEL_TOKEN": ("Vercel", "vercel"),
        }
        for env_var, (name, alias) in cases.items():
            with self.subTest(env_var):
                self.assertEqual(display_name({"env_vars": [env_var]}), name)
                self.assertEqual(derived_alias({"env_vars": [env_var]}), alias)


class KindHydrationTest(unittest.TestCase):
    """``kind`` is persisted data with an inference default, not derived."""

    def _write(self, tmp_path: Path, payload) -> Path:
        config_dir = tmp_path / ".config" / "swe"
        registry_file = config_dir / "providers.json"
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(json.dumps(payload))
        return registry_file

    def test_absent_kind_hydrates_to_inference(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(
                tmp_path,
                {"mine": {"env_vars": ["MINE_API_KEY"],
                          "key_filename": "mine"}},
            )
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                loaded = load_registry()
            self.assertEqual(loaded["mine"]["kind"], "inference")
            self.assertEqual(loaded["openai"]["kind"], "inference")
            self.assertEqual(loaded["vercel"]["kind"], "infra")

    def test_persisted_kind_survives_and_infra_defaults_merge(self):
        # A user registry from before infra existed gains the infra
        # defaults on next load; a kind it persisted stays as written.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(
                tmp_path,
                {"mine": {"env_vars": ["MINE_API_KEY"], "kind": "infra"},
                 "openai": {"env_vars": ["OPENAI_API_KEY"]}},
            )
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                loaded = load_registry()
            self.assertEqual(loaded["mine"]["kind"], "infra")
            self.assertEqual(loaded["openai"]["kind"], "inference")
            for name in DEFAULT_INFRA:
                self.assertIn(name, loaded)
                self.assertEqual(loaded[name]["kind"], "infra")

    def test_removed_infra_default_stays_removed(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(
                tmp_path,
                {"_removed": ["vercel"],
                 "openai": {"env_vars": ["OPENAI_API_KEY"]}},
            )
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                loaded = load_registry()
            self.assertNotIn("vercel", loaded)
            self.assertIn("netlify", loaded)

    def test_non_string_kind_raises_naming_the_row(self):
        from swe.configuration import CorruptConfigurationError

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            registry_file = self._write(
                tmp_path,
                {"mine": {"env_vars": ["MINE_API_KEY"], "kind": 5}},
            )
            p1, p2 = _registry_patches(tmp_path / ".config" / "swe", registry_file)
            with p1, p2:
                with self.assertRaises(CorruptConfigurationError) as ctx:
                    load_registry()
            self.assertIn("mine", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
