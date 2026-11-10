"""Real filesystem operations in scratch homes; no harness plugins are executed."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from tests._tempdir import temp_dir
from swe.plugins.actions import ActionError, plugin_action
from swe.plugins.catalog import build_catalog
from swe.plugins.local import local_plugins
from swe.plugins.managed import import_plugin
from swe.webapp.pages.plugins import explore, harness_actions


class LocalControls(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.registry = {}
        self.enterContext(patch('swe.harness.registry.load_registry_if_present',
                                side_effect=lambda: self.registry))
        self.root = self.home / '.config/opencode/plugins'
        self.root.mkdir(parents=True)
        self.file = self.root / 'review.ts'
        self.file.write_text('export const review = () => ({})\n')

    def row(self):
        return next(p for m in build_catalog(self.home)['marketplaces']
                    for p in m['plugins'] if 'opencode' in p['harnesses'])

    def test_suspend_restore_keeps_bytes_and_id(self):
        original = self.file.read_bytes()
        ref = self.row()['ref']
        self.assertEqual(self.row()['display_name'], 'review.ts')
        self.assertEqual(harness_actions(self.row()['harnesses']['opencode'], True),
                         ['disable', 'uninstall'])
        result = plugin_action(self.home, 'disable', ref, 'opencode')
        self.assertTrue(result['changed'])
        self.assertFalse(self.file.exists())
        parked = local_plugins(self.home)[0].path
        self.assertTrue(parked.is_relative_to(self.home / '.swe/data'))
        self.assertEqual(parked.read_bytes(), original)
        self.assertEqual(self.row()['ref'], ref)
        self.assertFalse(self.row()['harnesses']['opencode']['enabled'])
        self.assertFalse(plugin_action(self.home, 'disable', ref, 'opencode')['changed'])
        plugin_action(self.home, 'enable', ref, 'opencode')
        self.assertEqual(self.file.read_bytes(), original)
        self.assertFalse(parked.exists())

    def test_loose_file_appears_on_web_and_imports_into_swe(self):
        ref = self.row()['ref']
        self.assertIn(ref, explore(self.home)['plugins'])
        result = import_plugin(self.home, ref)
        from pathlib import Path
        owned = Path(result['state']['path'])
        self.assertEqual((owned / 'review.ts').read_bytes(), self.file.read_bytes())
        self.assertTrue(self.file.exists())

    def test_uninstall_active_and_suspended(self):
        ref = self.row()['ref']
        plugin_action(self.home, 'disable', ref, 'opencode')
        self.assertTrue(plugin_action(self.home, 'uninstall', ref, 'opencode')['changed'])
        self.assertEqual(local_plugins(self.home), [])
        self.file.write_text('active')
        plugin_action(self.home, 'uninstall', ref, 'opencode')
        self.assertFalse(self.file.exists())

    def test_collision_never_overwrites(self):
        ref = self.row()['ref']
        plugin_action(self.home, 'disable', ref, 'opencode')
        self.file.write_text('new copy')
        self.assertEqual(self.row()['harnesses']['opencode']['allowed_actions'], [])
        with self.assertRaises(ActionError):
            plugin_action(self.home, 'enable', ref, 'opencode')
        self.assertEqual(self.file.read_text(), 'new copy')

    def test_directory_and_symlink_removal_does_not_follow_target(self):
        target = self.home / 'source'
        target.mkdir()
        (target / 'index.ts').write_text('safe')
        link = self.root / 'linked'
        link.symlink_to(target, target_is_directory=True)
        ref = next(p.ref for p in local_plugins(self.home) if p.name == 'linked')
        plugin_action(self.home, 'disable', ref, 'opencode')
        plugin_action(self.home, 'uninstall', ref, 'opencode')
        self.assertEqual((target / 'index.ts').read_text(), 'safe')

    def test_relative_symlink_stays_visible_and_restores_verbatim(self):
        target = self.root.parent / 'source.js'
        target.write_text('safe')
        self.file.unlink()
        self.file.symlink_to('../source.js')
        ref = self.row()['ref']
        plugin_action(self.home, 'disable', ref, 'opencode')
        self.assertIsNotNone(self.row()['harnesses']['opencode']['path'])
        self.assertIn(ref, explore(self.home)['plugins'])
        plugin_action(self.home, 'enable', ref, 'opencode')
        self.assertEqual(os.readlink(self.file), '../source.js')
        self.assertEqual(self.file.read_text(), 'safe')

    def test_real_directory_round_trip_and_removal(self):
        folder = self.root / 'package'
        folder.mkdir()
        (folder / 'index.ts').write_text('plugin bytes')
        ref = next(p.ref for p in local_plugins(self.home) if p.name == 'package')
        plugin_action(self.home, 'disable', ref, 'opencode')
        plugin_action(self.home, 'enable', ref, 'opencode')
        self.assertEqual((folder / 'index.ts').read_text(), 'plugin bytes')
        plugin_action(self.home, 'uninstall', ref, 'opencode')
        self.assertFalse(folder.exists())
        self.assertTrue(self.file.exists())

    def test_symlinked_stash_cannot_move_files_outside_swe(self):
        plugin = local_plugins(self.home)[0]
        outside = self.home / 'outside'
        outside.mkdir()
        plugin.parked.parent.mkdir(parents=True)
        plugin.parked.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ActionError):
            plugin_action(self.home, 'disable', plugin.ref, 'opencode')
        self.assertTrue(self.file.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_cross_device_failure_leaves_original(self):
        with patch('swe.plugins.local.os.rename', side_effect=OSError(18, 'Cross-device link')):
            with self.assertRaises(ActionError):
                plugin_action(self.home, 'disable', self.row()['ref'], 'opencode')
        self.assertTrue(self.file.exists())

    def test_registry_root_and_unsupported_win(self):
        custom = self.home / 'custom'
        custom.mkdir()
        (custom / 'other.js').write_text('x')
        self.registry['opencode'] = {'capabilities': {'plugins': {'supported': True, 'root': str(custom)}}}
        self.assertEqual([p.name for p in local_plugins(self.home)], ['other.js'])
        self.registry['opencode']['capabilities']['plugins']['supported'] = False
        self.assertEqual(local_plugins(self.home), [])

    def test_stale_ref_does_not_target_a_different_registry_root(self):
        ref = self.row()['ref']
        custom = self.home / 'custom'
        custom.mkdir()
        other = custom / 'review.ts'
        other.write_text('different plugin')
        self.registry['opencode'] = {'capabilities': {'plugins': {'supported': True, 'root': str(custom)}}}
        with self.assertRaises(ActionError):
            plugin_action(self.home, 'uninstall', ref, 'opencode')
        self.assertTrue(other.exists())

    def test_install_and_unknown_refs_refused(self):
        with self.assertRaises(ActionError):
            plugin_action(self.home, 'install', self.row()['ref'], 'opencode')
        with self.assertRaises(ActionError):
            plugin_action(self.home, 'uninstall', 'missing@opencode-local', 'opencode')

    def test_empty_directories_and_dependencies_are_not_plugins(self):
        (self.root / 'empty').mkdir()
        deps = self.root / 'node_modules'
        deps.mkdir()
        (deps / 'dependency.js').write_text('x')
        self.assertEqual([p.name for p in local_plugins(self.home)], ['review.ts'])

    def test_malformed_mask_fails_closed(self):
        self.assertEqual(harness_actions({'installed': True, 'enabled': True,
                                         'allowed_actions': 'uninstall'}, True), [])


class DevinControls(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.registry = {}
        self.enterContext(patch('swe.harness.registry.load_registry_if_present',
                                side_effect=lambda: self.registry))
        self.root = self.home / '.local/share/devin/cli/plugins'
        self.root.mkdir(parents=True)
        self.copy = self.root / 'cache/github_review-abc/123'
        self.copy.mkdir(parents=True)
        self.entry = {'name': 'review', 'identity': 'github:review', 'version_dir': '123',
                      'spec': {'source': 'github'}}
        self.write([self.entry])

    def write(self, rows):
        (self.root / 'lock.json').write_text(json.dumps({'resolved': rows}))

    def row(self):
        return next(p for m in build_catalog(self.home)['marketplaces'] for p in m['plugins'])

    def test_native_remove_uses_unique_name_not_synthetic_ref(self):
        row = self.row()
        state = row['harnesses']['devin']
        self.assertIsNone(state['enabled'])
        self.assertEqual(harness_actions(state, True), ['uninstall'])
        def run(argv, **kwargs):
            self.assertEqual(argv, ['devin', 'plugins', 'remove', 'review', '--yes'])
            self.assertEqual(kwargs['cwd'], self.home)
            self.assertEqual(kwargs['env']['HOME'], str(self.home))
            self.assertNotIn('XDG_DATA_HOME', kwargs['env'])
            self.assertNotIn('--force', argv)
            self.write([])
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.dict(os.environ, {'XDG_DATA_HOME': '/other-account'}):
            result = plugin_action(self.home, 'uninstall', row['ref'], 'devin', run=run)
        self.assertTrue(result['changed'])
        self.assertFalse(result['state']['installed'])

    def test_no_fake_toggle_or_install(self):
        for action in ('install', 'enable', 'disable'):
            with self.assertRaises(ActionError):
                plugin_action(self.home, action, self.row()['ref'], 'devin')

    def test_ambiguous_names_have_no_actions(self):
        self.write([self.entry, {**self.entry, 'identity': 'url:other', 'spec': {'source': 'url'}}])
        for market in build_catalog(self.home)['marketplaces']:
            for row in market['plugins']:
                self.assertEqual(row['harnesses']['devin']['allowed_actions'], [])
                with self.assertRaises(ActionError):
                    plugin_action(self.home, 'uninstall', row['ref'], 'devin')

    def test_custom_root_is_read_only(self):
        custom = self.home / 'custom'
        custom.mkdir()
        (custom / 'lock.json').write_text((self.root / 'lock.json').read_text())
        self.registry['devin'] = {'capabilities': {'plugins': {'supported': True, 'root': str(custom)}}}
        self.assertEqual(self.row()['harnesses']['devin']['allowed_actions'], [])

    def test_bad_lock_reports_error(self):
        (self.root / 'lock.json').write_text('broken')
        self.assertTrue(build_catalog(self.home)['errors'])


if __name__ == '__main__':
    unittest.main()
