"""Exercise the real plugin sheet script with canned API responses."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'src/swe/webapp/static/pages/plugins.js'


@unittest.skipUnless(shutil.which('node'), 'node is required')
class PluginScripts(unittest.TestCase):
    def test_restriction_detail_is_visible_and_escaped(self):
        code = r'''
const registerPage = () => {};
const Fmt = { harnessName: x => x, logo: () => "" };
const esc = x => String(x).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
''' + SCRIPT.read_text() + r'''
const plugin = {ref: "review@kit", harnesses: {cursor: {
 status: "enabled", actions: [], control_detail: "Team policy <script>private</script>"}}};
console.log(actionsSection(plugin, {actions: ["cursor"]}));
'''
        result = subprocess.run(['node', '-e', code], check=True, text=True, capture_output=True)
        self.assertIn('Team policy &lt;script&gt;private&lt;/script&gt;', result.stdout)
        self.assertNotIn('<script>private</script>', result.stdout)

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
