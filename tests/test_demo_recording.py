"""scripts/demo_recording/: the take driver and the voiceover for the primitives demo (#524).

Recording needs the demo VM, so these check what can be checked without it:
the driver turns docs/demo.md into a script that runs every bash block of
segments 1 to 10 in order, the narration only anchors to events a take
logs, and the voiceover's timing rules.
"""

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import unittest
from pathlib import Path

from tests._tempdir import temp_dir

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "scripts" / "demo_recording"
DOC = ROOT / "docs" / "demo.md"
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"demo_recording_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HERE.exists() and DOC.exists(), "scripts/ or docs/ is not part of this source tree")
class DriverTest(unittest.TestCase):
    def setUp(self):
        self.driver = _load("driver")
        self.doc = DOC.read_text()

    def test_runs_every_block_of_the_ten_segments_in_order(self):
        blocks = self.driver.segment_blocks(self.doc)
        self.assertEqual(sorted(blocks), list(range(1, 11)))
        expected = [block for number in sorted(blocks) for block in blocks[number]]
        lines = self.driver.step_lines(self.doc)
        runs = [shlex.split(line[len("run "):])[0] for line in lines if line.startswith("run ")]
        self.assertEqual(runs, expected)
        segments = [line for line in lines if line.startswith("segment ")]
        self.assertEqual(segments, [f"segment {n} {self.driver.PLAN[n]}" for n in range(1, 11)])
        self.assertEqual(lines[-1], f"finish {self.driver.END}")

    def test_script_parses_and_sets_up_the_stage_first(self):
        script = self.driver.render(self.doc)
        self.assertIn('eval "$(scripts/demo_home.sh 2>/dev/null --root=/tmp/swe-demo --home="$HOME")"', script)
        self.assertLess(script.index("demo_home.sh"), script.index("segment 1 0"))
        if BASH:
            out = temp_dir(self) / "take.sh"
            out.write_text(script)
            run = subprocess.run([BASH, "-n", str(out)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_a_segment_whose_blocks_changed_stops_the_driver(self):
        doc = self.doc.replace("### 8. ", "### 8. \n\n```bash\necho extra\n```\n\n", 1)
        with self.assertRaises(SystemExit) as caught:
            self.driver.step_lines(doc)
        self.assertIn("segment 8 has 3 bash blocks", str(caught.exception))

    def test_writes_the_script_from_the_command_line(self):
        out = temp_dir(self) / "take.sh"
        self.driver.main(["driver.py", str(DOC), str(out)])
        self.assertTrue(out.read_text().startswith("#!/bin/bash\n"))


@unittest.skipUnless(HERE.exists(), "scripts/ is not part of this source tree")
class VoiceoverTest(unittest.TestCase):
    def setUp(self):
        self.voiceover = _load("voiceover")
        self.driver = _load("driver")

    def marks(self, *rows):
        return "\n".join(f"{name}\t{seconds}" for name, seconds in rows) + "\n"

    def test_parses_segment_command_and_page_events(self):
        events = self.voiceover.parse_marks(self.marks(
            ("segment 1", 0.0), ("cmd", 0.1), ("cmd", 11.7), ("view overview", 13.0),
            ("segment 2", 40.1), ("view agents", 40.1), ("cmd", 42.3), ("view agents", 50.0), ("end", 60.0)))
        self.assertEqual(events[(1, "start")], 0.0)
        self.assertEqual(events[(1, "cmd2")], 11.7)
        self.assertEqual(events[(2, "cmd1")], 42.3)
        # The first time a page is shown in a segment.
        self.assertEqual(events[(2, "view:agents")], 40.1)
        self.assertEqual(events["end"], 60.0)

    def test_lines_start_at_their_anchor_and_never_overlap(self):
        narration = {"title": "t", "words_per_second": 2.0, "cues": [
            {"segment": 1, "at": "start", "offset": 0.5, "text": "one two three four"},
            {"segment": 1, "at": "cmd1", "offset": 0.0, "text": "five six"},
            {"segment": 2, "at": "end", "offset": -3.0, "text": "seven"}]}
        events = self.voiceover.parse_marks(self.marks(
            ("segment 1", 0.0), ("cmd", 1.0), ("segment 2", 10.0), ("end", 20.0)))
        cues, warnings = self.voiceover.build(narration, events)
        self.assertEqual([c["start"] for c in cues], [0.5, 2.9, 17.0])
        self.assertEqual([c["duration"] for c in cues], [2.0, 1.0, 0.5])
        self.assertEqual(warnings, [])

    def test_warns_when_a_line_runs_into_the_next_segment(self):
        narration = {"title": "t", "words_per_second": 1.0, "cues": [
            {"segment": 1, "at": "start", "offset": 0.0, "text": "a b c d e f g h"}]}
        events = self.voiceover.parse_marks(self.marks(("segment 1", 0.0), ("segment 2", 5.0), ("end", 9.0)))
        _, warnings = self.voiceover.build(narration, events)
        self.assertEqual(len(warnings), 1)
        self.assertIn("runs 3.4s into what follows", warnings[0])

    def test_an_anchor_the_take_never_logged_is_an_error(self):
        narration = {"title": "t", "words_per_second": 2.5, "cues": [
            {"segment": 1, "at": "cmd3", "offset": 0.0, "text": "x"}]}
        events = self.voiceover.parse_marks(self.marks(("segment 1", 0.0), ("cmd", 1.0), ("end", 5.0)))
        with self.assertRaises(SystemExit):
            self.voiceover.build(narration, events)

    def test_the_narration_only_anchors_to_events_the_driver_logs(self):
        narration = json.loads((HERE / "narration.json").read_text())
        logged = {"end"}
        for number, steps in self.driver.STEPS.items():
            logged.add((number, "start"))
            runs = sum(1 for kind, _ in steps if kind == "run")
            logged.update((number, f"cmd{i}") for i in range(1, runs + 1))
            logged.update((number, f"view:{page}") for kind, page in steps if kind == "view")
        for cue in narration["cues"]:
            anchor = "end" if cue["at"] == "end" else (cue["segment"], cue["at"])
            self.assertIn(anchor, logged, cue["text"])

    def test_writes_json_and_markdown(self):
        scratch = temp_dir(self)
        (scratch / "marks.tsv").write_text(self.marks(("segment 1", 0.0), ("cmd", 1.0), ("end", 30.0)))
        (scratch / "narration.json").write_text(json.dumps({"title": "Demo", "words_per_second": 2.5, "cues": [
            {"segment": 1, "at": "start", "offset": 0.5, "text": "Hello there."}]}))
        self.voiceover.main(["voiceover.py", str(scratch / "narration.json"), str(scratch / "marks.tsv"),
                             str(scratch / "out")])
        data = json.loads((scratch / "out" / "voiceover.json").read_text())
        self.assertEqual(data["video"], "swe-demo-voiceless.mov")
        self.assertEqual(data["cues"], [{"segment": 1, "start": 0.5, "duration": 0.8, "text": "Hello there."}])
        self.assertIn("- **0:00.5** (1s) Hello there.", (scratch / "out" / "voiceover.md").read_text())


if __name__ == "__main__":
    unittest.main()
