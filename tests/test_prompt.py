"""Tests for interactive prompt helpers."""

import io
import unittest
from unittest.mock import patch

from swe.prompt import read_line


class ReadLineTest(unittest.TestCase):
    def test_non_tty_accepts_lf(self):
        fake = io.StringIO("description\n")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "description")

    def test_non_tty_accepts_cr(self):
        fake = io.StringIO("save\r")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "save")

    def test_non_tty_accepts_crlf(self):
        fake = io.StringIO("tags\r\n")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "tags")

    def test_non_tty_multiple_cr_lines(self):
        fake = io.StringIO("description\rHello CR\rsave\r")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "description")
            self.assertEqual(read_line(""), "Hello CR")
            self.assertEqual(read_line(""), "save")

class ByteReaderTest(unittest.TestCase):
    """_read_line_bytes on a real pipe: CR pushback, editing keys, echo."""

    def _pipe(self, payload: bytes) -> int:
        import os

        r, w = os.pipe()
        os.write(w, payload)
        os.close(w)
        return r

    def test_a_cr_followed_by_a_letter_pushes_the_letter_back(self):
        import os

        from swe import prompt

        fd = self._pipe(b"a\rx")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(fd), "a")
                self.assertEqual(prompt._read_line_bytes(fd), "x")
        finally:
            prompt._pushback.clear()
            os.close(fd)

    def test_a_select_failure_after_cr_still_ends_the_line(self):
        import os

        from swe import prompt

        fd = self._pipe(b"a\r")
        try:
            with patch("sys.stdout", io.StringIO()), \
                 patch("select.select", side_effect=OSError("no poll")):
                self.assertEqual(prompt._read_line_bytes(fd), "a")
        finally:
            os.close(fd)

    def test_backspace_edits_and_echoes_on_a_tty_without_echo(self):
        import os

        from swe import prompt

        fd = self._pipe(b"ab\x7fc\n")
        out = io.StringIO()
        try:
            with patch("sys.stdout", out), \
                 patch.object(prompt, "_tty_echo_on", return_value=False):
                self.assertEqual(prompt._read_line_bytes(fd, echo=True), "ac")
            self.assertIn("\b \b", out.getvalue())
        finally:
            os.close(fd)

    def test_ctrl_c_raises_and_ctrl_d_ends_like_eof(self):
        import os

        from swe import prompt

        fd = self._pipe(b"\x03")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertRaises(KeyboardInterrupt,
                                  prompt._read_line_bytes, fd)
        finally:
            os.close(fd)

        fd = self._pipe(b"\x04")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertRaises(EOFError, prompt._read_line_bytes, fd)
        finally:
            os.close(fd)

        fd = self._pipe(b"ab\x04")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(fd), "ab")
        finally:
            os.close(fd)

    def test_a_cr_at_a_still_open_pipe_ends_the_line(self):
        """Writer not closed: select reports nothing readable after the
        CR, so no partner byte is consumed and the line still ends."""
        import os

        from swe import prompt

        r, w = os.pipe()
        os.write(w, b"a\r")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(r), "a")
            os.write(w, b"b\n")
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(r), "b")
        finally:
            os.close(w)
            os.close(r)

    def test_backspace_on_an_empty_line_is_a_noop(self):
        import os

        from swe import prompt

        fd = self._pipe(b"\x7fok\n")
        try:
            with patch("sys.stdout", io.StringIO()), \
                 patch.object(prompt, "_tty_echo_on", return_value=True):
                self.assertEqual(prompt._read_line_bytes(fd, echo=True), "ok")
        finally:
            os.close(fd)

        # echo=False (a pipe): the erasure is not written to stdout.
        fd = self._pipe(b"ab\x7fc\n")
        out = io.StringIO()
        try:
            with patch("sys.stdout", out):
                self.assertEqual(prompt._read_line_bytes(fd, echo=False), "ac")
            self.assertEqual(out.getvalue(), "")
        finally:
            os.close(fd)

    def test_control_characters_are_skipped(self):
        import os

        from swe import prompt

        fd = self._pipe(b"a\x01b\n")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(fd), "ab")
        finally:
            os.close(fd)

    def test_cr_only_line_ends_write_the_newline_the_tty_skipped(self):
        """A CR without a paired LF and without terminal echo still needs
        the cursor moved off the row."""
        import os

        from swe import prompt

        # Without TTY echo the reader echoes "x" itself, then the newline;
        # with it, the TTY drew "x" and only the newline is missing.
        # An LF means the line end already moved the cursor: no newline.
        cases = [(b"x\r", False, "x\n"), (b"x\r", True, "\n"),
                 (b"x\n", False, "x"), (b"x\r\n", True, "")]
        for payload, tty_echo, written in cases:
            out = io.StringIO()
            fd2 = self._pipe(payload)
            try:
                with patch("sys.stdout", out), \
                     patch.object(prompt, "_tty_echo_on",
                                  return_value=tty_echo):
                    self.assertEqual(prompt._read_line_bytes(fd2), "x")
                self.assertEqual(out.getvalue(), written, (payload, tty_echo))
            finally:
                os.close(fd2)

    def test_crlf_piped_input_does_not_eat_every_other_line(self):
        """A TextIOWrapper on a pipe has no peek() and its tell()/seek()
        raise OSError, so the old reader consumed the LF of every CRLF and
        could not put it back: every second read_line saw that leftover LF
        and returned an empty string."""
        import os

        r, w = os.pipe()
        os.write(w, b"a\r\nb\r\nlast\r\n")
        os.close(w)
        fake = io.TextIOWrapper(io.FileIO(r, "r"))
        try:
            with patch("sys.stdin", fake), patch("sys.stdout", io.StringIO()):
                self.assertEqual(read_line(""), "a")
                self.assertEqual(read_line(""), "b")
                self.assertEqual(read_line(""), "last")
                self.assertRaises(EOFError, read_line, "")
        finally:
            fake.close()

    def test_pipe_input_is_not_echoed_to_stdout(self):
        import os

        r, w = os.pipe()
        os.write(w, b"quiet\n")
        os.close(w)
        fake = io.TextIOWrapper(io.FileIO(r, "r"))
        out = io.StringIO()
        try:
            with patch("sys.stdin", fake), patch("sys.stdout", out):
                self.assertEqual(read_line(""), "quiet")
        finally:
            fake.close()
        self.assertEqual(out.getvalue(), "")

    def test_pushback_does_not_drop_byte_after_cr(self):
        """CR followed by a non-LF byte must not eat the next character."""
        from swe import prompt as prompt_mod

        prompt_mod._pushback.clear()
        # "ab\rcd\n" — after CR, 'c' must start next line, not be dropped
        data = list(b"ab\rcd\n")

        def fake_read(_fd, _n=1):
            if not data:
                return b""
            return bytes([data.pop(0)])

        # Patch builtins used inside _read_line_bytes (local import of os)
        import os as real_os

        with patch.object(prompt_mod, "_restore_cooked_tty"), patch.object(
            prompt_mod, "_tty_echo_on", return_value=False
        ), patch.object(real_os, "read", side_effect=fake_read), patch(
            "select.select", return_value=([0], [], [])
        ), patch("sys.stdout", new=io.StringIO()):
            prompt_mod._pushback.clear()
            line1 = prompt_mod._read_line_bytes(0)
            line2 = prompt_mod._read_line_bytes(0)
        self.assertEqual(line1, "ab")
        self.assertEqual(line2, "cd")


    def test_eof_raises(self):
        fake = io.StringIO("")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            with self.assertRaises(EOFError):
                read_line("")



class _FdStdin:
    """A stdin with a real fd (a pipe) whose tty-ness the test decides."""

    encoding = "utf-8"

    def __init__(self, fd: int, tty: bool = False, text: str = ""):
        self._fd, self._tty = fd, tty
        self._text = io.StringIO(text)

    def fileno(self):
        return self._fd

    def isatty(self):
        return self._tty

    def read(self, n=-1):
        return self._text.read(n)


class ReadLineFdTest(unittest.TestCase):
    def setUp(self):
        import os

        from swe import prompt

        self.addCleanup(prompt._pushback.clear)
        self.addCleanup(prompt._stream_pushback.clear)
        r, w = os.pipe()
        self.r, self.w = r, w
        self.addCleanup(lambda: [os.close(fd) for fd in (r, w) if _is_open(fd)])

    def _feed(self, data: bytes):
        import os

        os.write(self.w, data)
        os.close(self.w)

    def test_the_prompt_is_written_and_a_pipe_is_read_by_bytes(self):
        self._feed(b"yes\n")
        out = io.StringIO()
        with patch("sys.stdin", _FdStdin(self.r)), patch("sys.stdout", out):
            self.assertEqual(read_line("Continue? "), "yes")
        self.assertEqual(out.getvalue(), "Continue? ")

    def test_a_tty_gets_cooked_mode_back_before_reading(self):
        import termios

        self._feed(b"ok\n")
        attrs = [0, 0, 0, 0, 9600, 9600, []]
        with patch("sys.stdin", _FdStdin(self.r, tty=True)), \
                patch("sys.stdout", io.StringIO()), \
                patch.object(termios, "tcgetattr", return_value=list(attrs)), \
                patch.object(termios, "tcsetattr") as tcsetattr:
            self.assertEqual(read_line(), "ok")
        fd, when, new = tcsetattr.call_args.args
        self.assertEqual((fd, when), (self.r, termios.TCSANOW))
        self.assertTrue(new[0] & termios.ICRNL)
        self.assertTrue(new[1] & termios.ONLCR)
        self.assertTrue(new[3] & termios.ICANON and new[3] & termios.ECHO)

    def test_cooked_mode_failures_never_stop_the_read(self):
        import sys
        import termios

        from swe import prompt

        prompt._restore_cooked_tty(self.r)  # a pipe: tcgetattr fails, nothing raises
        with patch.object(termios, "tcgetattr", return_value=[0] * 6 + [[]]), \
                patch.object(termios, "tcsetattr", side_effect=termios.error(25, "ENOTTY")):
            prompt._restore_cooked_tty(self.r)
        with patch.dict(sys.modules, {"termios": None}):
            prompt._restore_cooked_tty(self.r)  # no termios at all (Windows)

    def test_a_byte_reader_failure_falls_back_to_the_text_stream(self):
        from swe import prompt

        self._feed(b"")
        with patch("sys.stdin", _FdStdin(self.r, text="from text\n")), \
                patch.object(prompt, "_read_line_bytes", side_effect=OSError(9, "Bad file")):
            self.assertEqual(read_line(), "from text")

    def test_an_interrupted_read_is_retried(self):
        from swe import prompt

        reads = iter([InterruptedError(), b"z"])

        def read(_fd, _n):
            item = next(reads)
            if isinstance(item, BaseException):
                raise item
            return item

        with patch("os.read", read):
            self.assertEqual(prompt._read_byte(self.r), b"z")


def _is_open(fd: int) -> bool:
    import os

    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


class StreamEofTest(unittest.TestCase):
    def test_a_last_line_without_a_terminator_is_returned_then_eof(self):
        fake = io.StringIO("no newline")
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "no newline")
            with self.assertRaises(EOFError):
                read_line("")


if __name__ == "__main__":
    unittest.main()
