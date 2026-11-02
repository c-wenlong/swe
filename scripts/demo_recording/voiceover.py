"""Time the demo's narration against one recorded take.

    python3 scripts/demo_recording/voiceover.py narration.json marks.tsv OUT_DIR

narration.json holds the lines to say, each anchored to an event in the take:
a segment's `start`, its Nth command (`cmd1`, `cmd2`, ...), the first time a
page is shown in it (`view:agents`), or the take's `end`, plus an offset in
seconds. marks.tsv is what the take's driver logged. A line starts at its
anchor plus offset, and never before the previous line has ended. Writes
voiceover.json (for a narration tool) and voiceover.md (to read), and prints
a warning for any line that would run into the next segment.
"""

import json
import sys
from pathlib import Path

VIDEO = "swe-demo-voiceless.mov"


def parse_marks(text):
    """{(segment, anchor): seconds} and {"end": seconds} from marks.tsv."""
    events, segment, commands = {}, None, 0
    for line in text.splitlines():
        name, seconds = line.split("\t")
        seconds = float(seconds)
        if name.startswith("segment "):
            segment, commands = int(name.split()[1]), 0
            events[(segment, "start")] = seconds
        elif name == "cmd":
            commands += 1
            events[(segment, f"cmd{commands}")] = seconds
        elif name.startswith("view "):
            events.setdefault((segment, "view:" + name.split()[1]), seconds)
        elif name == "end":
            events["end"] = seconds
    return events


def build(narration, events):
    """(cues, warnings): each cue's start and spoken length in seconds."""
    words_per_second = narration["words_per_second"]
    starts = {key[0]: t for key, t in events.items() if key != "end" and key[1] == "start"}
    cues, warnings, previous_end = [], [], 0.0
    for cue in narration["cues"]:
        anchor = "end" if cue["at"] == "end" else (cue["segment"], cue["at"])
        if anchor not in events:
            raise SystemExit(f"voiceover: segment {cue['segment']} has no {cue['at']!r} in the take")
        start = max(events[anchor] + cue["offset"], previous_end + 0.4)
        length = len(cue["text"].split()) / words_per_second
        limit = starts.get(cue["segment"] + 1, events["end"])
        if start + length > limit + 0.5:
            warnings.append(f"segment {cue['segment']}: \"{cue['text'][:40]}...\" runs "
                            f"{start + length - limit:.1f}s into what follows")
        cues.append({"segment": cue["segment"], "start": round(start, 1),
                     "duration": round(length, 1), "text": cue["text"]})
        previous_end = start + length
    return cues, warnings


def clock(seconds):
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def markdown(narration, cues, events):
    starts = {key[0]: t for key, t in events.items() if key != "end" and key[1] == "start"}
    lines = [f"# Voiceover: {narration['title']}", "",
             f"For `{VIDEO}` ({clock(events['end'])}). Times are from the start of the video; "
             f"lengths assume {narration['words_per_second'] * 60:.0f} words a minute. "
             "`voiceover.json` has the same lines for a narration tool."]
    segment = None
    for cue in cues:
        if cue["segment"] != segment:
            segment = cue["segment"]
            lines += ["", f"## Segment {segment} (from {clock(starts[segment])})", ""]
        lines.append(f"- **{clock(cue['start'])}** ({cue['duration']:.0f}s) {cue['text']}")
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) != 4:
        raise SystemExit("usage: voiceover.py narration.json marks.tsv OUT_DIR")
    narration = json.loads(Path(argv[1]).read_text())
    events = parse_marks(Path(argv[2]).read_text())
    cues, warnings = build(narration, events)
    out = Path(argv[3])
    out.mkdir(parents=True, exist_ok=True)
    (out / "voiceover.json").write_text(json.dumps(
        {"title": narration["title"], "video": VIDEO, "duration": events["end"],
         "words_per_second": narration["words_per_second"], "cues": cues}, indent=2) + "\n")
    (out / "voiceover.md").write_text(markdown(narration, cues, events))
    print(f"{len(cues)} lines over {clock(events['end'])}")
    for warning in warnings:
        print("warning:", warning)


if __name__ == "__main__":
    main(sys.argv)
