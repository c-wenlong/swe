import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from swe.reports.runners import (
    REPORT_ENV_MARKER,
    REPORT_ENV_OUTPUT_KIND,
    REPORT_ENV_RUN_ID,
    RunnerError,
    RunnerOutputError,
    RunnerSpec,
    RunnerTimeoutError,
    UnsupportedHarnessError,
    build_argv,
    run_structured,
)


SUMMARY = {
    "kind": "session_summary_batch",
    "project_path": "/work/project",
    "sessions": [
        {
            "session_id": "s1",
            "objective": "Fix the build",
            "outcome": "Tests pass",
            "status": "completed",
            "changes": ["Updated parser"],
            "decisions": [],
            "blockers": [],
            "follow_ups": [],
            "context": "No further action",
        }
    ],
    "project_summary": "Build fixed",
}


class RunnerArgvTest(unittest.TestCase):
    def test_claude_argv_is_non_interactive_and_read_only(self):
        spec = RunnerSpec("Claude", "claude-sonnet", ("--verbose",))
        self.assertEqual(
            build_argv(spec),
            [
                "claude",
                "--print",
                "--output-format",
                "json",
                "--model",
                "claude-sonnet",
                "--no-session-persistence",
                "--permission-mode",
                "plan",
                "--verbose",
            ],
        )

    def test_codex_argv_is_non_interactive_and_read_only(self):
        spec = RunnerSpec("codex", "gpt-5", ("--config", "model_reasoning=medium"))
        self.assertEqual(
            build_argv(spec),
            [
                "codex",
                "exec",
                "--model",
                "gpt-5",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--json",
                "--config",
                "model_reasoning=medium",
                "-",
            ],
        )

    def test_unsupported_harness(self):
        with self.assertRaisesRegex(UnsupportedHarnessError, "droid"):
            build_argv(RunnerSpec("droid", "model"))

    def test_cannot_override_safety_flags(self):
        with self.assertRaisesRegex(ValueError, "managed by SWE"):
            build_argv(RunnerSpec("codex", "gpt-5", ("--sandbox=danger-full-access",)))

    def test_other_spellings_of_the_unsafe_flags_are_refused(self):
        for harness, args in (
            ("codex", ("--dangerously-bypass-approvals-and-sandbox",)),
            ("codex", ("-s", "danger-full-access")),
            ("codex", ("-sdanger-full-access",)),
            ("codex", ("-c", "sandbox_mode=danger-full-access")),
            ("codex", ("--config=sandbox_mode=danger-full-access",)),
            ("codex", ("-c", "approval_policy=never")),
            ("codex", ("-capproval_policy=never",)),
            ("codex", ("-csandbox_mode=danger-full-access",)),
            ("codex", ("-c=sandbox_mode=danger-full-access",)),
            ("claude", ("--allow-dangerously-skip-permissions",)),
        ):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, "managed by SWE"):
                    build_argv(RunnerSpec(harness, "model", args))

    def test_claude_rejects_option_terminator(self):
        with self.assertRaisesRegex(ValueError, "terminators"):
            build_argv(RunnerSpec("claude", "sonnet", ("--", "ignored")))

    def test_codex_rejects_option_terminator(self):
        with self.assertRaisesRegex(ValueError, "terminators"):
            build_argv(RunnerSpec("codex", "gpt-5", ("--", "ignored")))

    def test_claude_rejects_positional_argument(self):
        with self.assertRaisesRegex(ValueError, "positional argument"):
            build_argv(RunnerSpec("claude", "sonnet", ("unexpected",)))

    def test_codex_rejects_extra_stdin_marker(self):
        with self.assertRaisesRegex(ValueError, "terminators"):
            build_argv(RunnerSpec("codex", "gpt-5", ("-",)))

    def test_useful_option_value_pairs_remain_supported(self):
        claude_argv = build_argv(
            RunnerSpec("claude", "sonnet", ("--effort", "medium"))
        )
        codex_argv = build_argv(
            RunnerSpec("codex", "gpt-5", ("--config", "model_reasoning=medium"))
        )

        self.assertEqual(claude_argv[-2:], ["--effort", "medium"])
        glued = build_argv(RunnerSpec("codex", "gpt-5", ("-cmodel_reasoning=medium",)))
        self.assertEqual(glued[-2:], ["-cmodel_reasoning=medium", "-"])
        self.assertEqual(codex_argv[-3:], ["--config", "model_reasoning=medium", "-"])

    def test_managed_flags_cannot_be_repeated_after_safety_flags(self):
        for harness, args in (
            ("claude", ("--no-session-persistence",)),
            ("codex", ("--ephemeral",)),
        ):
            with self.subTest(harness=harness):
                with self.assertRaisesRegex(ValueError, "managed by SWE"):
                    build_argv(RunnerSpec(harness, "model", args))


class RunStructuredTest(unittest.TestCase):
    def completed(self, stdout, *, returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    @patch("swe.reports.runners.subprocess.run")
    def test_passes_prompt_on_stdin_and_marks_environment(self, run):
        run.return_value = self.completed(json.dumps(SUMMARY))

        result = run_structured(
            RunnerSpec("claude", "sonnet", timeout_seconds=42, cwd="/work/project"),
            "summarize this",
            output_kind="session_summary_batch",
        )

        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(result.attempts, 1)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], "summarize this")
        self.assertEqual(kwargs["timeout"], 42)
        self.assertEqual(kwargs["cwd"], "/work/project")
        self.assertTrue(kwargs["text"])
        self.assertFalse(kwargs["check"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"][REPORT_ENV_MARKER], "1")
        self.assertEqual(kwargs["env"][REPORT_ENV_OUTPUT_KIND], "session_summary_batch")
        self.assertRegex(kwargs["env"][REPORT_ENV_RUN_ID], r"^[0-9a-f]{32}$")

    @patch("swe.reports.runners.subprocess.run")
    def test_extracts_claude_result_json(self, run):
        run.return_value = self.completed(json.dumps({"result": json.dumps(SUMMARY)}))
        result = run_structured(
            RunnerSpec("claude", "sonnet"), "prompt", output_kind="session_summary_batch"
        )
        self.assertEqual(result.data, SUMMARY)

    @patch("swe.reports.runners.subprocess.run")
    def test_extracts_codex_jsonl_agent_message(self, run):
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(SUMMARY)},
        }
        run.return_value = self.completed(
            json.dumps({"type": "thread.started"}) + "\n" + json.dumps(event) + "\n"
        )
        result = run_structured(
            RunnerSpec("codex", "gpt-5"), "prompt", output_kind="session_summary_batch"
        )
        self.assertEqual(result.data, SUMMARY)

    @patch("swe.reports.runners.subprocess.run")
    def test_rejects_malformed_json_without_retry(self, run):
        run.return_value = self.completed("not json")
        with self.assertRaisesRegex(RunnerOutputError, "malformed"):
            run_structured(
                RunnerSpec("codex", "gpt-5"),
                "prompt",
                output_kind="session_summary_batch",
            )
        self.assertEqual(run.call_count, 1)

    @patch("swe.reports.runners.subprocess.run")
    def test_rejects_invalid_schema(self, run):
        invalid = dict(SUMMARY, sessions=[])
        run.return_value = self.completed(json.dumps(invalid))
        with self.assertRaisesRegex(RunnerOutputError, "non-empty"):
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="session_summary_batch",
            )

    @patch("swe.reports.runners.subprocess.run")
    def test_validates_final_report_and_user_owned_follow_up_status(self, run):
        report = {
            "kind": "final_report",
            "markdown": "# Work Completed\n\n- Fixed parser",
            "follow_up_suggestions": [
                {
                    "action": "create",
                    "follow_up_id": "",
                    "text": "Run CI",
                    "project_path": "/work/project",
                    "evidence": ["session:s1"],
                    "status": "done",
                }
            ],
        }
        run.return_value = self.completed(json.dumps(report))
        with self.assertRaisesRegex(RunnerOutputError, "user-owned status"):
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="final_report",
            )

    @patch("swe.reports.runners.subprocess.run")
    def test_timeout_retries_once_then_stops(self, run):
        run.side_effect = subprocess.TimeoutExpired(["codex"], 1)
        with self.assertRaisesRegex(RunnerTimeoutError, "2 attempts"):
            run_structured(
                RunnerSpec("codex", "gpt-5", timeout_seconds=1),
                "prompt",
                output_kind="session_summary_batch",
                sleep=Mock(),
            )
        self.assertEqual(run.call_count, 2)

    @patch("swe.reports.runners.subprocess.run")
    def test_transient_failure_retries_once_and_succeeds(self, run):
        run.side_effect = [
            self.completed("", returncode=1, stderr="429 rate limit"),
            self.completed(json.dumps(SUMMARY)),
        ]
        result = run_structured(
            RunnerSpec("claude", "sonnet"),
            "prompt",
            output_kind="session_summary_batch",
            sleep=Mock(),
        )
        self.assertEqual(result.attempts, 2)
        self.assertEqual(run.call_count, 2)

    @patch("swe.reports.runners.subprocess.run")
    def test_second_transient_failure_stops(self, run):
        run.return_value = self.completed("", returncode=1, stderr="service unavailable")
        with self.assertRaises(RunnerError):
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="session_summary_batch",
                sleep=Mock(),
            )
        self.assertEqual(run.call_count, 2)

    @patch("swe.reports.runners.subprocess.run")
    def test_claude_stdout_auth_error_is_actionable(self, run):
        run.return_value = self.completed(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 401,
                    "result": "Failed to authenticate. OAuth access token has been revoked.",
                }
            ),
            returncode=1,
        )

        with self.assertRaises(RunnerError) as raised:
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="session_summary_batch",
                sleep=Mock(),
            )

        message = str(raised.exception)
        self.assertIn("OAuth access token has been revoked", message)
        self.assertIn("claude auth login", message)
        self.assertNotIn("no diagnostic output", message)
        self.assertEqual(run.call_count, 1)

    @patch("swe.reports.runners.subprocess.run")
    def test_error_redacts_tokens_and_sensitive_argument_values(self, run):
        run.return_value = self.completed(
            "",
            returncode=1,
            stderr="request used sk-ant-abcdefghijklmnopqrstuvwxyz and hunter2",
        )
        spec = RunnerSpec("claude", "sonnet", ("--api-key", "hunter2"))
        with self.assertRaises(RunnerError) as raised:
            run_structured(spec, "prompt", output_kind="session_summary_batch")
        message = str(raised.exception)
        self.assertNotIn("sk-ant-", message)
        self.assertNotIn("hunter2", message)
        self.assertIn("[REDACTED]", message)



class RunnerEdgeTest(unittest.TestCase):
    """Spec validation, diagnostics pulled from stdout, output decoding and
    the schema checks, one refusal at a time."""

    def completed(self, stdout, *, returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def run_with(self, stdout, *, returncode=0, stderr="", harness="claude",
                 args=(), kind="session_summary_batch"):
        with patch("swe.reports.runners.subprocess.run",
                   return_value=self.completed(stdout, returncode=returncode,
                                               stderr=stderr)) as run:
            result = run_structured(RunnerSpec(harness, "m", args), "p",
                                    output_kind=kind, sleep=Mock())
        return result, run

    def failure(self, stdout, **kwargs):
        with self.assertRaises(RunnerError) as raised:
            self.run_with(stdout, **kwargs)
        return str(raised.exception)

    def test_spec_refuses_an_empty_model_or_timeout(self):
        with self.assertRaisesRegex(ValueError, "model must not be empty"):
            RunnerSpec("claude", "  ")
        for timeout in (0, -1):
            with self.assertRaisesRegex(ValueError, "greater than zero"):
                RunnerSpec("claude", "m", timeout_seconds=timeout)
        spec = RunnerSpec("  Claude ", "m", ["--verbose"])
        self.assertEqual((spec.harness, spec.args), ("claude", ("--verbose",)))

    def test_codex_config_overrides_that_lift_the_sandbox_are_refused(self):
        for args in (("-c", "sandbox_mode=danger-full-access"),
                     ("-csandbox_mode=x",),
                     ("--config=approval_policy=never",),
                     ("-c", "'sandbox_workspace_write.network_access'=true")):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, "managed by SWE"):
                    build_argv(RunnerSpec("codex", "m", args))
        argv = build_argv(RunnerSpec("codex", "m", ("-c", "model_reasoning_effort=high")))
        self.assertEqual(argv[-3:], ["-c", "model_reasoning_effort=high", "-"])

    def test_no_diagnostic_at_all_says_so(self):
        message = self.failure("", returncode=2)
        self.assertIn("exit code 2: no diagnostic output", message)
        # A JSON record that is not an error says nothing either.
        message = self.failure(json.dumps({"type": "result", "result": "ok"}),
                               returncode=2)
        self.assertIn("no diagnostic output", message)

    def test_a_stdout_error_object_names_its_message_or_type(self):
        cases = {
            json.dumps({"type": "error", "error": {"message": "overloaded"}}): "overloaded",
            json.dumps({"type": "error", "error": {"type": "invalid_request"}}): "invalid_request",
            json.dumps({"type": "error", "error": "quota exhausted"}): "quota exhausted",
            json.dumps({"is_error": True}): "structured harness error",
        }
        for stdout, detail in cases.items():
            with self.subTest(detail):
                self.assertIn(f"exit code 1: {detail}",
                              self.failure(stdout, returncode=1, harness="codex"))

    def test_the_last_error_line_of_jsonl_wins_and_status_is_named(self):
        stdout = "\n".join([
            "not json",
            json.dumps(["a", "list"]),
            json.dumps({"type": "error", "message": "first"}),
            json.dumps({"api_error_status": 500, "message": "upstream sk-abcdefghijklm"}),
        ])
        message = self.failure(stdout, returncode=1, harness="codex")
        self.assertIn("HTTP 500: upstream [REDACTED]", message)
        self.assertNotIn("first", message)

    def test_the_auth_hint_is_claude_only_and_never_doubled(self):
        message = self.failure("", returncode=1, stderr="401 Unauthorized",
                               harness="codex")
        self.assertNotIn("claude auth login", message)
        message = self.failure(
            "", returncode=1,
            stderr="Unauthorized: run claude auth login again")
        self.assertEqual(message.count("claude auth login"), 1)

    def test_an_inline_sensitive_value_is_redacted(self):
        message = self.failure("", returncode=1, stderr="bad key hunter22",
                               args=("--token=hunter22",))
        self.assertNotIn("hunter22", message)
        self.assertIn("[REDACTED]", message)

    def test_a_signal_exit_is_retried_once(self):
        with patch("swe.reports.runners.subprocess.run", side_effect=[
                self.completed("", returncode=-9),
                self.completed(json.dumps(SUMMARY))]) as run:
            result = run_structured(RunnerSpec("claude", "m"), "p",
                                    output_kind="session_summary_batch",
                                    sleep=Mock())
        self.assertEqual((result.attempts, run.call_count), (2, 2))

    def test_a_missing_or_unstartable_harness_is_a_runner_error(self):
        spec = RunnerSpec("claude", "m", ("--api-key", "hunter22"))
        with patch("swe.reports.runners.subprocess.run",
                   side_effect=FileNotFoundError("claude")):
            with self.assertRaisesRegex(RunnerError, "executable not found: claude"):
                run_structured(spec, "p", output_kind="final_report")
        with patch("swe.reports.runners.subprocess.run",
                   side_effect=PermissionError("denied hunter22")):
            with self.assertRaises(RunnerError) as raised:
                run_structured(spec, "p", output_kind="final_report")
        self.assertIn("Unable to start claude", str(raised.exception))
        self.assertNotIn("hunter22", str(raised.exception))

    def test_output_decoding_finds_the_payload_where_each_harness_puts_it(self):
        payloads = {
            "structured_output": json.dumps({"structured_output": SUMMARY}),
            "fenced result": json.dumps(
                {"result": "```json\n" + json.dumps(SUMMARY) + "\n```"}),
            "result object": json.dumps({"result": SUMMARY}),
            "codex item": "\n".join([
                json.dumps({"item": {"type": "agent_message", "text": "{bad"}}),
                json.dumps({"item": {"type": "agent_message",
                                     "text": json.dumps(SUMMARY)}}),
                json.dumps({"type": "turn.completed"}),
            ]),
        }
        for label, stdout in payloads.items():
            with self.subTest(label):
                result, _ = self.run_with(stdout, harness="codex")
                self.assertEqual(result.data, SUMMARY)

    def test_output_without_a_payload_is_malformed(self):
        for stdout in ("", json.dumps({"result": 7}), json.dumps([SUMMARY]),
                       json.dumps({"item": {"type": "reasoning", "text": "{}"}})):
            with self.subTest(stdout=stdout):
                with self.assertRaisesRegex(RunnerOutputError, "malformed or missing"):
                    self.run_with(stdout)

    def test_each_summary_field_is_checked(self):
        def broken(**changes):
            data = json.loads(json.dumps(SUMMARY))
            session = data["sessions"][0]
            for key, value in changes.items():
                (data if key in data else session)[key] = value
            return data

        cases = {
            "got 'final_report'": dict(kind="final_report"),
            "'project_summary' must be a string": dict(project_summary=None),
            "'sessions' must be non-empty": dict(sessions=[]),
            "must be an object": dict(sessions=["s1"]),
            "'objective' must be a string": dict(objective=3),
            "'status' must be completed": dict(status="done"),
            "'changes' must be a list of strings": dict(changes=[1]),
            "'blockers' must be a list of strings": dict(blockers="none"),
        }
        for message, changes in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(RunnerOutputError, message):
                    self.run_with(json.dumps(broken(**changes)))

    def test_each_final_report_field_is_checked(self):
        good = {"action": "create", "follow_up_id": "f1", "text": "t",
                "project_path": "/p", "evidence": ["e"]}

        def report(markdown="# R", suggestions=None):
            return json.dumps({"kind": "final_report", "markdown": markdown,
                               "follow_up_suggestions": [good] if suggestions is None
                               else suggestions})

        result, _ = self.run_with(report(), kind="final_report")
        self.assertEqual(result.data["markdown"], "# R")
        cases = {
            "markdown must not be empty": report(markdown="  "),
            "must be a list of objects": report(suggestions=["x"]),
            "'text' must be a string": report(suggestions=[{**good, "text": None}]),
            "action must be create": report(suggestions=[{**good, "action": "delete"}]),
            "'evidence' must be a list": report(suggestions=[{**good, "evidence": "e"}]),
        }
        for message, stdout in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(RunnerOutputError, message):
                    self.run_with(stdout, kind="final_report")

    def test_an_unknown_output_kind_is_refused(self):
        with self.assertRaisesRegex(RunnerOutputError, "Unknown structured output kind"):
            self.run_with(json.dumps({"kind": "mystery"}), kind="mystery")


if __name__ == "__main__":
    unittest.main()
