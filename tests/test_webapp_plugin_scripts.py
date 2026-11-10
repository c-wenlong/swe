"""Exercise the real plugin sheet script with canned API responses."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'src/swe/webapp/static/pages/plugins.js'


@unittest.skipUnless(shutil.which('node'), 'node is required')
class PluginScripts(unittest.TestCase):
    def test_changed_action_keeps_service_detail(self):
        code = '''
const registerPage = () => {};
const currentRoute = () => "elsewhere";
const Fmt = { harnessName: x => x };
const apiPost = async () => ({status: 200, data: {explore: {},
  result: {changed: true, detail: "Restart existing sessions; team plugin remains"}}});
'''
        code += SCRIPT.read_text()
        code += '''
(async () => {
  await runAction({verb: "uninstall", ref: "review@kit", harness: "opencode"});
  console.log(JSON.stringify(page.actOutcome));
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', code], check=True, text=True, capture_output=True)
        self.assertIn('Restart existing sessions; team plugin remains', json.loads(result.stdout)['text'])


if __name__ == '__main__':
    unittest.main()
