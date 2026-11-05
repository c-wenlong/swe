import unittest
from unittest.mock import patch

from swe.reports.batching import (
    SummaryBatch,
    SummaryInput,
    _trim_both,
    _trim_text,
    build_summary_batches,
    compact_transcript,
    make_summary_input,
)
from swe.reports.transcripts import NormalizedMessage, NormalizedTranscript
from swe.sessions.models import Session


def item(session_id, project, timestamp, chars=20):
    session = Session(timestamp, "Codex", project, session_id=session_id, tool_name="codex")
    transcript = NormalizedTranscript(
        "codex",
        session_id,
        project,
        [NormalizedMessage("human", "x" * chars), NormalizedMessage("assistant", "done")],
    )
    return SummaryInput(session, transcript, transcript.digest, project)


class ReportBatchingTest(unittest.TestCase):
    def test_batches_are_chronological_and_project_isolated(self):
        batches = build_summary_batches(
            [item("b", "/b", 3), item("a2", "/a", 2), item("a1", "/a", 1)]
        )
        self.assertEqual([batch.project_root for batch in batches], ["/a", "/b"])
        self.assertEqual([entry.session.session_id for entry in batches[0].inputs], ["a1", "a2"])

    def test_count_and_character_limits_split_batches(self):
        inputs = [item(str(index), "/repo", index, chars=20) for index in range(5)]
        by_count = build_summary_batches(inputs, max_sessions=2, max_chars=10_000)
        self.assertEqual([len(batch.inputs) for batch in by_count], [2, 2, 1])
        by_chars = build_summary_batches(inputs[:2], max_sessions=25, max_chars=30)
        self.assertEqual([len(batch.inputs) for batch in by_chars], [1, 1])

    def test_dedicated_inputs_always_receive_one_call(self):
        dedicated = item("large", "/repo", 1)
        batches = build_summary_batches([], [dedicated])
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].dedicated)
        self.assertEqual(batches[0].inputs, (dedicated,))

    def test_thousand_sessions_scale_by_project_batches(self):
        inputs = [item(str(index), f"/repo-{index // 100}", index, chars=1) for index in range(1000)]
        batches = build_summary_batches(inputs)
        self.assertEqual(len(batches), 40)
        self.assertTrue(all(len(batch.inputs) == 25 for batch in batches))

    def test_compaction_keeps_context_edges_and_tool_outcome(self):
        source = NormalizedTranscript(
            "codex",
            "large",
            "/repo",
            [
                NormalizedMessage("human", "BEGIN " + "a" * 300),
                NormalizedMessage("assistant", "middle " + "b" * 300),
                NormalizedMessage("tool", "TESTS PASSED " + "c" * 30, kind="tool"),
                NormalizedMessage("assistant", "d" * 300 + " END"),
            ],
        )
        original_digest = source.digest
        compacted = compact_transcript(source, 300)
        self.assertLessEqual(len(compacted.normalized_text), 300)
        self.assertIn("BEGIN", compacted.normalized_text)
        self.assertIn("END", compacted.normalized_text)
        self.assertIn("TESTS PASSED", compacted.normalized_text)
        self.assertEqual(source.digest, original_digest)
        self.assertNotEqual(compacted.digest, original_digest)


def transcript(*messages):
    return NormalizedTranscript(
        "codex", "s", "/repo",
        [NormalizedMessage(role, text, kind=kind) for role, text, kind in messages],
        source_paths=["/logs/s.jsonl"],
    )


class CompactionEdgeTest(unittest.TestCase):
    MARKER = "\n[...compacted...]\n"

    def test_trim_text_keeps_the_start_or_the_end(self):
        text = "0123456789" * 5
        self.assertEqual(_trim_text(text, 100), text)
        head = _trim_text(text, 30)
        self.assertEqual(len(head), 30)
        self.assertTrue(head.startswith("0123456789"))
        self.assertTrue(head.endswith(self.MARKER))
        tail = _trim_text(text, 30, keep_end=True)
        self.assertEqual(len(tail), 30)
        self.assertTrue(tail.startswith(self.MARKER))
        self.assertTrue(tail.endswith("789"))
        # A budget too small for the marker cuts without one.
        self.assertEqual(_trim_text(text, 5), "01234")
        self.assertEqual(_trim_text(text, 5, keep_end=True), "56789")

    def test_trim_both_keeps_both_edges(self):
        text = "BEGIN" + "x" * 100 + "END"
        both = _trim_both(text, 40)
        self.assertEqual(len(both), 40)
        self.assertTrue(both.startswith("BEGIN"))
        self.assertTrue(both.endswith("END"))
        self.assertIn(self.MARKER, both)
        self.assertEqual(_trim_both(text, 200), text)
        self.assertEqual(_trim_both(text, 10), "BEGINxxxxx")
        # One character past the marker: all of it goes to the start.
        one = _trim_both(text, len(self.MARKER) + 1)
        self.assertEqual(one, "B" + self.MARKER)

    def test_a_non_positive_budget_is_refused(self):
        for budget in (0, -1):
            with self.assertRaisesRegex(ValueError, "must be positive"):
                compact_transcript(transcript(("human", "x", "message")), budget)

    def test_a_short_transcript_comes_back_as_is(self):
        source = transcript(("human", "hi", "message"))
        self.assertIs(compact_transcript(source, 1000), source)

    def test_one_long_message_keeps_its_start_and_end(self):
        source = transcript(("human", "BEGIN " + "a" * 5000 + " END", "message"))
        compacted = compact_transcript(source, 400)
        text = compacted.normalized_text
        self.assertLessEqual(len(text), 400)
        self.assertTrue(text.startswith("human: BEGIN"))
        self.assertTrue(text.endswith(" END"))
        self.assertIn("[...compacted...]", text)
        # Provenance survives compaction.
        self.assertEqual(compacted.source_paths, ["/logs/s.jsonl"])
        self.assertEqual((compacted.tool_name, compacted.session_id,
                          compacted.project_path), ("codex", "s", "/repo"))

    def test_many_messages_keep_both_ends_and_drop_the_middle(self):
        messages = [("human" if i % 2 == 0 else "assistant", f"m{i:03d} " + "z" * 40,
                     "message") for i in range(100)]
        compacted = compact_transcript(transcript(*messages), 1000)
        text = compacted.normalized_text
        self.assertLessEqual(len(text), 1000)
        self.assertIn("m000", text)
        self.assertIn("m099", text)
        self.assertNotIn("m050", text)
        # Messages keep their original order.
        import re
        kept = [int(n) for m in compacted.messages
                for n in re.findall(r"m(\d{3})", m.text)]
        self.assertEqual(kept, sorted(kept))

    def test_tool_output_has_its_own_share_of_the_budget(self):
        messages = [("human", "ask " + "h" * 2000, "message"),
                    ("assistant", "answer " + "a" * 2000, "message")]
        messages += [("tool", f"TOOL{i} " + "t" * 300, "tool") for i in range(10)]
        compacted = compact_transcript(transcript(*messages), 1000)
        tools = [m for m in compacted.messages if m.role == "tool"]
        self.assertTrue(tools)
        self.assertTrue(tools[0].text.startswith("TOOL0"))
        tool_chars = sum(len(m.role) + len(m.text) + 4 for m in tools)
        self.assertLessEqual(tool_chars, 1000 // 5)
        self.assertLessEqual(len(compacted.normalized_text), 1000)

    def test_roles_outside_the_conversation_and_tools_are_dropped(self):
        source = transcript(("system", "s" * 500, "message"),
                            ("human", "keep me", "message"))
        compacted = compact_transcript(source, 100)
        self.assertEqual([m.role for m in compacted.messages], ["human"])

    def test_the_result_never_exceeds_the_budget(self):
        # However the budget splits between context and tools, the
        # separators the per-message costs approximate never push it over.
        for budget in (50, 120, 333, 1000):
            for count in (2, 7, 40):
                with self.subTest(budget=budget, count=count):
                    messages = [("human", "q" * 30, "message"),
                                ("assistant", "r" * 30, "message")] * count
                    messages += [("tool", "t" * 30, "tool")] * count
                    compacted = compact_transcript(transcript(*messages), budget)
                    self.assertLessEqual(len(compacted.normalized_text), budget)


class BatchEdgeTest(unittest.TestCase):
    def test_limits_must_be_positive(self):
        for kwargs in ({"max_sessions": 0}, {"max_chars": 0}, {"max_sessions": -1}):
            with self.subTest(**kwargs):
                with self.assertRaisesRegex(ValueError, "must be positive"):
                    build_summary_batches([item("a", "/a", 1)], **kwargs)

    def test_an_input_larger_than_the_char_limit_still_gets_a_batch(self):
        big = item("big", "/a", 1, chars=500)
        small = item("small", "/a", 2)
        batches = build_summary_batches([small, big], max_chars=100)
        self.assertEqual([[i.session.session_id for i in b.inputs] for b in batches],
                         [["big"], ["small"]])
        self.assertEqual([b.batch_id for b in batches],
                         ["project:/a:1", "project:/a:2"])

    def test_dedicated_batches_follow_project_ones_in_a_stable_order(self):
        batches = build_summary_batches(
            [item("p", "/a", 1)],
            [item("z", "/b", 1), item("y", "/a", 5), item("x", "/a", 2)])
        self.assertEqual([b.batch_id for b in batches], [
            "project:/a:1",
            "dedicated:codex:x:1",
            "dedicated:codex:y:2",
            "dedicated:codex:z:3",
        ])
        self.assertEqual([b.dedicated for b in batches], [False, True, True, True])

    def test_token_estimate_overcounts_with_prompt_overhead(self):
        batch = SummaryBatch("b", "/a", (item("a", "/a", 1, chars=300),))
        chars = batch.normalized_chars
        self.assertEqual(chars, len("human: " + "x" * 300 + "\n\nassistant: done"))
        self.assertEqual(batch.estimated_input_tokens, (chars + 2) // 3 + 1_500)
        self.assertEqual(SummaryBatch("e", "/a", ()).estimated_input_tokens, 1_500)

    def test_make_summary_input_resolves_the_project_and_digest(self):
        session = Session(1, "Codex", "/repo/sub", session_id="s", tool_name="codex")
        source = transcript(("human", "x", "message"))
        with patch("swe.reports.batching.resolve_project_root",
                   return_value="/repo") as resolve:
            made = make_summary_input(session, source)
            pinned = make_summary_input(session, source, digest="pinned")
        resolve.assert_called_with("/repo/sub")
        self.assertEqual(made.project_root, "/repo")
        self.assertEqual(made.digest, source.digest)
        self.assertEqual(pinned.digest, "pinned")


if __name__ == "__main__":
    unittest.main()
