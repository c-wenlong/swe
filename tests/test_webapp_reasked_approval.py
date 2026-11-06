"""An approval asked again under the same request id (a satellite resends a
refused batch whole, so the journal holds the ask twice): the web chat keeps
one card, at the newest ask while it is open, and leaves an answered card
where it is. The Mac and iPhone chats follow the same rule."""

import shutil
import unittest

from tests.test_webapp_live_reply import ChatScript
from tests.test_webapp_scrollback import SETUP

ASK = "({ type: 'approval.requested', seq: %d, data: { request_id: 'r1', prompt: 'Run it?' } })"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestReaskedApproval(ChatScript):
    def run_js(self, body):
        return super().run_js(SETUP + body)

    def test_an_open_card_moves_to_the_newest_ask(self):
        out = self.run_js("""
            T.appendEvent(msg(1, 'user', 'fix it'), false);
            T.appendEvent(%s, false);
            T.appendEvent(msg(3, 'user', 'fix it'), false);
            T.appendEvent(%s, false);
            T.scrollBottom();
            const cards = entries().filter((c) => c.classList.contains('approval'));
            return { cards: cards.length, last: entries().indexOf(cards[0]) === entries().length - 1 };""" % (ASK % 2, ASK % 4))
        self.assertEqual(out, {"cards": 1, "last": True})

    def test_an_answered_card_stands(self):
        out = self.run_js("""
            T.appendEvent(%s, false);
            T.appendEvent({ type: 'approval.resolved', seq: 3, data: { request_id: 'r1', decision: 'allow' } }, false);
            T.appendEvent(msg(4, 'assistant', 'done'), false);
            T.appendEvent(%s, false);
            T.scrollBottom();
            const cards = entries().filter((c) => c.classList.contains('approval'));
            return { cards: cards.length, resolved: cards[0].classList.contains('resolved'), at: entries().indexOf(cards[0]) };""" % (ASK % 2, ASK % 5))
        self.assertEqual(out, {"cards": 1, "resolved": True, "at": 0})


if __name__ == "__main__":
    unittest.main()
