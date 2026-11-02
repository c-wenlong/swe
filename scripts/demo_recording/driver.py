"""Write the shell script that records one take of docs/demo.md in the demo VM.

    python3 scripts/demo_recording/driver.py docs/demo.md OUT.sh

The script runs in Terminal inside the VM (Screen Recording and Accessibility
are granted to Terminal there), and its own window is the terminal on camera.
It lays out SWE.app on the left two thirds and Terminal on the right third,
starts the recorder, then for each segment of the doc types every bash block
after a `$` prompt, runs it, and clicks SWE.app to the page the segment talks
about. A segment never starts before its time in the doc's table; one that
runs long pushes the next one back. Every segment start, command and page
switch goes to marks.tsv, which voiceover.py times the narration from.

The commands are the doc's own, so a change to a block changes the take. The
page clicks below are not: they are screen positions in SWE.app at the
window size this script sets, and need measuring again if the sidebar or a
page moves.
"""

import re
import shlex
import sys
from pathlib import Path

# Each segment's start in the doc's table, in seconds, and the take's end.
PLAN = {1: 0, 2: 40, 3: 65, 4: 85, 5: 115, 6: 145, 7: 160, 8: 195, 9: 220, 10: 250}
END = 300

# SWE.app's sidebar rows (x 80) with its window at {0, 33}, 1280 by 1047.
SIDEBAR = {"overview": 159, "plugins": 607, "skills": 641, "agents": 675, "mcp": 709,
           "hooks": 743, "settings": 949}
REFRESH = (1221, 87)  # a page's header Refresh button
PLUGINS_MARKETPLACE = (1208, 335)  # expands the hub's marketplace on Explore
SETTINGS_FILES = (289, 473)  # Settings > Files
HUB_SYNC_REFRESH = (1198, 792)  # the Hub sync card's refresh, under Files


def view(page):
    return ("view", page)


def click(point):
    return ("click", point)


# What each segment does, in order: ("run", n) runs its nth bash block.
STEPS = {
    1: [("run", 0), ("run", 1), view("overview"), ("pause", 2)],
    2: [view("agents"), ("pause", 2), ("run", 0)],
    3: [view("skills"), ("pause", 2), ("run", 0)],
    4: [view("plugins"), ("pause", 1.5), click(PLUGINS_MARKETPLACE), ("pause", 2), ("run", 0)],
    5: [view("mcp"), ("pause", 2), ("run", 0)],
    6: [view("hooks"), ("pause", 2), ("run", 0)],
    # The AGENTS.md page reads the strip again only when it is opened, so
    # after the build it is left and opened again.
    7: [("run", 0), view("agents"), ("pause", 3), ("run", 1), view("skills"), ("pause", 0.6),
        view("agents"), ("pause", 2)],
    8: [("run", 0), ("run", 1)],
    9: [("run", 0), ("run", 1), view("hooks"), ("pause", 1), click(REFRESH), ("pause", 2),
        ("run", 2)],
    10: [view("settings"), ("pause", 1), click(SETTINGS_FILES), ("pause", 2), ("run", 0),
         click(HUB_SYNC_REFRESH), ("pause", 2), ("run", 1), ("run", 2), click(HUB_SYNC_REFRESH)],
}


def setup_block(doc):
    """The first bash block under "Before you record"."""
    section = doc.split("## Before you record", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"```bash\n(.*?)```", section, re.S)[0].rstrip("\n")


def segment_blocks(doc):
    """{segment number: [bash block, ...]} for the doc's numbered segments."""
    section = doc.split("## Segments", 1)[1].split("\n## ", 1)[0]
    parts = re.split(r"\n### (\d+)\. ", section)[1:]
    return {int(number): [block.rstrip("\n") for block in re.findall(r"```bash\n(.*?)```", body, re.S)]
            for number, body in zip(parts[0::2], parts[1::2])}


def step_lines(doc):
    """The take as lines of the script's own small commands."""
    blocks = segment_blocks(doc)
    if sorted(blocks) != sorted(STEPS):
        raise SystemExit(f"driver: docs/demo.md has segments {sorted(blocks)}, the driver knows {sorted(STEPS)}")
    lines = []
    for number, steps in STEPS.items():
        used = {arg for kind, arg in steps if kind == "run"}
        if used != set(range(len(blocks[number]))):
            raise SystemExit(f"driver: segment {number} has {len(blocks[number])} bash blocks; "
                             f"the driver runs {sorted(used)}. Update STEPS.")
        lines.append(f"segment {number} {PLAN[number]}")
        for kind, arg in steps:
            if kind == "run":
                lines.append("run " + shlex.quote(blocks[number][arg]))
            elif kind == "view":
                lines.append(f"view {arg} 80 {SIDEBAR[arg]}")
            elif kind == "click":
                lines.append(f"click {arg[0]} {arg[1]}")
            else:
                lines.append(f"pause {arg}")
    lines.append(f"finish {END}")
    return lines


HEAD = r'''#!/bin/bash
# One voiceless take of docs/demo.md, written by
# scripts/demo_recording/driver.py. Runs in Terminal inside the demo VM.
eval "$(/opt/homebrew/bin/brew shellenv)"; export PATH="$HOME/.local/bin:$PATH"
OUT=/tmp/take; mkdir -p "$OUT"; rm -f "$OUT"/take.mov "$OUT"/marks.tsv "$OUT"/stop "$OUT"/done
SCREENREC="$HOME/Library/Application Support/swe-demo/screenrec"

osa() { osascript -e "$1" >/dev/null 2>&1; }
me="$(osascript -e 'tell application "Terminal" to id of front window')"
# Only this window: any other would show behind the app.
osa "tell application \"Terminal\" to repeat with w in (every window whose id is not $me)
  close w
end repeat"
# Font first: Terminal keeps its column count when the font changes, so
# setting it after the bounds would grow the window under the app.
osa "tell application \"Terminal\" to set font size of current settings of window id $me to 16"
osa "tell application \"Terminal\" to set bounds of window id $me to {1280, 33, 1920, 1080}"
defaults write com.apple.dock autohide -bool true; defaults write com.apple.dock autohide-delay -float 1000; killall Dock
open ~/Applications/SWE.app; sleep 3
osa 'tell application "System Events" to tell process "SWE"
  set position of window 1 to {0, 33}
  set size of window 1 to {1280, 1047}
end tell'
click() { osa "tell application \"System Events\" to tell process \"SWE\" to click at {$1, $2}"; }
click 80 159

# Off camera, as docs/demo.md's "Before you record".
cd ~/swe
'''

TAIL = r'''
now() { python3 -c 'import time; print(f"{time.time():.2f}")'; }
since() { python3 -c "print(f'{$(now) - $T0:.1f}')"; }
mark() { printf '%s\t%s\n' "$1" "$(since)" >> "$OUT/marks.tsv"; }
type_out() {
  local text="$1" i ch
  printf '\033[1;32m$\033[0m '
  for ((i = 0; i < ${#text}; i++)); do
    ch="${text:i:1}"
    if [ "$ch" = $'\n' ]; then printf '\n\033[2m>\033[0m '; else printf '%s' "$ch"; fi
    sleep 0.03
  done
  printf '\n'
}
run() { mark "cmd"; type_out "$1"; sleep 0.4; eval "$1"; echo; sleep 1; }
view() { mark "view $1"; click "$2" "$3"; }
pause() { sleep "$1"; }
# A segment never starts before its planned time; a long one just runs on.
segment() {
  sleep "$(python3 -c "print(max(0, $T0 + $2 - $(now)))")"
  mark "segment $1"
}
finish() {
  sleep "$(python3 -c "print(max(3, $T0 + $1 - $(now)))")"
  mark "end"
  touch "$OUT/stop"; wait "$REC"
}

clear
"$SCREENREC" "$OUT/take.mov" "$OUT/stop" > "$OUT/screenrec.log" 2>&1 &
REC=$!
until grep -q recording "$OUT/screenrec.log" 2>/dev/null; do
  kill -0 "$REC" 2>/dev/null || { echo failed > "$OUT/done"; exit 1; }
  sleep 0.2
done
sleep 1.5
T0="$(now)"
'''


def render(doc):
    setup = setup_block(doc).replace('eval "$(scripts/demo_home.sh', 'eval "$(scripts/demo_home.sh 2>/dev/null')
    return HEAD + setup + "\n" + TAIL + "\n".join(step_lines(doc)) + '\necho done > "$OUT/done"\n'


def main(argv):
    if len(argv) != 3:
        raise SystemExit("usage: driver.py docs/demo.md OUT.sh")
    Path(argv[2]).write_text(render(Path(argv[1]).read_text()))


if __name__ == "__main__":
    main(sys.argv)
