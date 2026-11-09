"""The T0 counter heuristics in ``insights/metrics.py``: a session's
index rows in, the ``session_metrics`` counter fields out. Every pattern
is a port of the audit scripts, so the tests pin the shapes the audit
corpus actually showed (worktree refusals, ``<tool_use_error>``,
``Command timed out``, trivial acks, injected-turn boilerplate)."""

import unittest

from swe.insights import metrics
from swe.insights.metrics import MessageRow as R


def compute(rows, session_id="sess-1", harness="claude"):
    return metrics.compute(session_id, harness, rows)


class CounterTests(unittest.TestCase):
    def test_empty_session(self):
        m = compute([])
        self.assertEqual(m.n_messages, 0)
        self.assertEqual(m.n_tool_calls, 0)
        self.assertIsNone(m.machine_share)
        self.assertIsNone(m.first_ts)
        self.assertEqual(m.is_dead, 0)
        self.assertEqual(m.is_probe, 0)

    def test_messages_and_first_ts(self):
        m = compute([
            R("user", "2026-10-01T10:00:05.000Z", user_text="fix the bug"),
            R("assistant", "2026-10-01T10:00:01.000Z", assistant_text="ok"),
            R("user", None, user_text="and the test"),
        ])
        self.assertEqual(m.n_messages, 3)
        # first_ts is epoch seconds of the earliest stamp.
        self.assertEqual(m.first_ts, 1790848801)

    def test_tool_calls_are_call_shaped_rows(self):
        m = compute([
            R("tool", None, tool_text="Bash: git status"),
            R("tool", None, tool_text="On branch main\nnothing to commit"),
            R("tool", None, tool_text="mcp__notion__API-post-page"),
            R("tool", None, tool_text="read: /tmp/x.py"),
            R("tool", None, tool_text="File created successfully at: /tmp/x"),
        ])
        self.assertEqual(m.n_tool_calls, 3)
        self.assertEqual(m.n_tool_errors, 0)

    def test_error_signatures_count_as_errors_not_calls(self):
        m = compute([
            R("tool", None, tool_text="<tool_use_error>File has not been read yet"),
            R("tool", None, tool_text="Exit code 1\nTraceback (most recent call last):"),
            R("tool", None, tool_text="Tool 'edit' validation failed: String not found"),
            R("tool", None, tool_text="PreToolUse:Bash hook error: [python3 hook]"),
            R("tool", None, tool_text="Permission for this command was denied by a built-in rule"),
        ])
        self.assertEqual(m.n_tool_errors, 5)
        self.assertEqual(m.n_tool_calls, 0)

    def test_a_call_whose_argument_has_an_error_phrase_is_a_call(self):
        m = compute([
            R("tool", None, tool_text="Bash: grep -rn 'not found' src/"),
            R("tool", None, tool_text="exec: timeout 30 make test"),
            R("tool", None, tool_text="Bash: python3 -c 'print(\"error: x\")'"),
        ])
        self.assertEqual(m.n_tool_calls, 3)
        self.assertEqual(m.n_tool_errors, 0)
        self.assertEqual(m.n_timeouts, 0)

    def test_a_merged_call_and_result_row_counts_the_failure(self):
        # opencode writes "name: args\nresult" as one indexed row; the
        # call-shaped first line must not hide the failed result.
        m = compute([
            R("tool", None, tool_text="read: /repo/x.yml\n"
                                      "Error: File not found"),
            R("tool", None, tool_text="rf__yt_getTranscript\n"
                                      "Error: Resume token is not valid"),
        ])
        self.assertEqual(m.n_tool_errors, 2)
        self.assertEqual(m.n_tool_calls, 0)
        self.assertEqual(m.n_mcp_errors, 0)

    def test_a_prefixed_result_with_a_strong_signature_still_errors(self):
        # devin results carry the tool name: "exec: Output from command
        # in shell X:..." — call-shaped, but a real failure inside.
        m = compute([
            R("tool", None, tool_text="exec: Output from command in shell a1:\n"
                                      "Exit code 1\nmake: *** failed"),
            R("tool", None, tool_text="exec: Output from command in shell b2:\n"
                                      "Traceback (most recent call last):"),
        ])
        self.assertEqual(m.n_tool_errors, 2)
        self.assertEqual(m.n_tool_calls, 0)

    def test_plain_output_with_error_words_is_not_an_error(self):
        m = compute([
            R("tool", None, tool_text="grep found 3 matches for error handling"),
            R("tool", None, tool_text="def handle_failed_job(job):\n    retry(job)"),
        ])
        self.assertEqual(m.n_tool_errors, 0)

    def test_ack_unblocks_need_an_assistant_row_first(self):
        m = compute([
            R("user", None, user_text="yes"),            # opener, not an unblock
            R("assistant", None, assistant_text="Shall I commit?"),
            R("user", None, user_text="go ahead"),
            R("user", None, user_text="ok"),             # same stop counts once
            R("assistant", None, assistant_text="Want me to push?"),
            R("tool", None, tool_text="Bash: git status"),  # tool rows don't rearm
            R("user", None, user_text="ship it"),
            R("assistant", None, assistant_text="done"),
            R("user", None, user_text="yes, but rewrite it all"),
        ])
        self.assertEqual(m.n_ack_unblocks, 2)

    def test_machine_share_is_the_machine_fraction_of_user_rows(self):
        m = compute([
            R("user", None, user_text="<task-notification>job done</task-notification>"),
            R("user", None, user_text="<system-reminder>context low</system-reminder>"),
            R("user", None, user_text="Repo: /x\nYou are a fixer. Ship it."),
            R("user", None, user_text="fix the login button"),
        ])
        self.assertAlmostEqual(m.machine_share, 0.75)

    def test_no_user_rows_leaves_machine_share_null(self):
        m = compute([R("assistant", None, assistant_text="hi")])
        self.assertIsNone(m.machine_share)

    def test_a_machine_messages_later_chunks_stay_machine(self):
        # the indexer splits a long message into rows sharing one ts;
        # only the first chunk carries the leading tag.
        ts = "2026-10-01T10:00:00.000Z"
        m = compute([
            R("user", ts, user_text="<task-notification>\n" + "x" * 100),
            R("user", ts, user_text="continuation with no tag at all"),
            R("user", "2026-10-01T10:00:01.000Z", user_text="fix the bug"),
        ])
        self.assertAlmostEqual(m.machine_share, 2 / 3)
        # the untagged chunk is not a real prompt, so the session is dead
        # only on the human line, not the notification tail.
        self.assertEqual(m.is_dead, 1)

    def test_a_newer_ts_breaks_the_machine_chunk_chain(self):
        ts = "2026-10-01T10:00:00.000Z"
        m = compute([
            R("user", ts, user_text="<task-notification>\n" + "x" * 100),
            R("user", ts, user_text="continuation with no tag at all"),
            R("user", "2026-10-01T10:00:01.000Z",
              user_text="plain prompt, a new second"),
        ])
        self.assertAlmostEqual(m.machine_share, 2 / 3)


class GuardAndFailureTests(unittest.TestCase):
    def test_worktree_refusal_counts_as_guard_and_error(self):
        m = compute([
            R("tool", None,
              tool_text="This agent is isolated in the worktree "
                        "/repo/.claude/worktrees/agent-x, but this command "
                        "is too complex to verify that it stays inside the "
                        "worktree. Refusing to run it — a worktree-isolated "
                        "agent's git operations must target its own."),
            R("tool", None, tool_text="Refusing to write through symlink: /x"),
        ])
        self.assertEqual(m.n_guard_refusals, 2)
        self.assertEqual(m.n_tool_errors, 2)

    def test_timeouts(self):
        m = compute([
            R("tool", None, tool_text="Exit code 143\nCommand timed out after 2m 0s"),
            R("tool", None, tool_text="the request timed out"),
            R("tool", None, tool_text="deadline exceeded while waiting"),
            R("tool", None, tool_text="Command did not complete within "
                                      "its 120s timeout"),
        ])
        self.assertEqual(m.n_timeouts, 4)
        self.assertEqual(m.n_tool_errors, 4)

    def test_a_file_full_of_timeout_words_is_not_a_timeout(self):
        # a successful read/result whose payload mentions the word
        m = compute([
            R("tool", None, tool_text="read: /repo/cfg.yml\n"
                                      "timeout = 30\ndeadline: friday"),
            R("tool", None, tool_text="--timeout <n>  set the timeout"),
        ])
        self.assertEqual(m.n_timeouts, 0)
        self.assertEqual(m.n_tool_errors, 0)
        self.assertEqual(m.n_tool_calls, 1)

    def test_mcp_errors(self):
        m = compute([
            R("tool", None, tool_text="MCP error -32602: Input validation error"),
            R("tool", None, tool_text="<tool_use_error>Error: No such tool available: mcp__x__y"),
            R("tool", None, tool_text="tabs_context_mcp returned an error."),
            R("tool", None, tool_text="Exit code 1\nplain shell failure"),
        ])
        self.assertEqual(m.n_mcp_errors, 3)
        self.assertEqual(m.n_tool_errors, 4)

    def test_a_call_row_naming_mcp_is_not_an_error(self):
        m = compute([R("tool", None, tool_text="mcp__notion__API-post-page: {}")])
        self.assertEqual(m.n_mcp_errors, 0)
        self.assertEqual(m.n_tool_errors, 0)
        self.assertEqual(m.n_tool_calls, 1)


class FlagTests(unittest.TestCase):
    def test_probe_is_a_lone_ping_with_no_work(self):
        for text in ("hi", "PONG", "test", "ping", "are you there?"):
            m = compute([
                R("user", None, user_text=text),
                R("assistant", None, assistant_text="Hi! What can I do for you?"),
            ])
            self.assertEqual(m.is_probe, 1, msg=text)
            self.assertEqual(m.is_dead, 0)

    def test_probe_needs_the_ping_to_be_alone_and_idle(self):
        # a second prompt makes it a real session
        m = compute([
            R("user", None, user_text="hi"),
            R("assistant", None, assistant_text="Hi!"),
            R("user", None, user_text="now refactor the store"),
        ])
        self.assertEqual(m.is_probe, 0)
        # a "hi" that kicked off tool work is not a probe
        m = compute([
            R("user", None, user_text="hi"),
            R("tool", None, tool_text="Bash: ls"),
            R("assistant", None, assistant_text="You have 3 files."),
        ])
        self.assertEqual(m.is_probe, 0)
        # a real first prompt is not a ping
        m = compute([R("user", None, user_text="hi, rewrite the parser")])
        self.assertEqual(m.is_probe, 0)

    def test_dead_means_no_answer_after_the_last_real_prompt(self):
        # a prompt that ends the session is dead
        m = compute([R("user", None, user_text="fix the thing")])
        self.assertEqual(m.is_dead, 1)
        # any agent output after the prompt answers it, text or tool
        m = compute([
            R("user", None, user_text="fix the thing"),
            R("assistant", None, assistant_text="on it"),
        ])
        self.assertEqual(m.is_dead, 0)
        m = compute([
            R("user", None, user_text="fix the thing"),
            R("tool", None, tool_text="Bash: make test"),
        ])
        self.assertEqual(m.is_dead, 0)
        # tool work before the prompt does not answer it
        m = compute([
            R("tool", None, tool_text="Bash: ls"),
            R("user", None, user_text="is anyone there"),
        ])
        self.assertEqual(m.is_dead, 1)
        # machine boilerplate is not a prompt, and does not answer one
        m = compute([
            R("user", None, user_text="fix the thing"),
            R("user", None, user_text="<task-notification>done</task-notification>"),
        ])
        self.assertEqual(m.is_dead, 1)
        m = compute([R("user", None, user_text="<task-notification>x</task-notification>")])
        self.assertEqual(m.is_dead, 0)
        m = compute([R("assistant", None, assistant_text="resumed mid-flight")])
        self.assertEqual(m.is_dead, 0)


if __name__ == "__main__":
    unittest.main()
