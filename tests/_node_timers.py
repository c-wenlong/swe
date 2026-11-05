"""A manual clock for node tests of the web app's page scripts.

``FAKE_TIMERS`` defines ``fakeTimers()``. Called at the top of a test's
script, it swaps ``setTimeout``/``clearTimeout`` for a queue and returns
``advance(ms)``, which runs every timer that falls due in that time, in
order (a timer set by one that runs is run too, once it is due). A test
then steps a debounce, throttle or grace period exactly, instead of
sleeping for it on the wall clock.
"""

FAKE_TIMERS = r"""
// A manual clock, for a test that waits on a page's own timers: once
// installed, setTimeout only queues, and advance(ms) runs what falls due
// in that time, in order, so nothing depends on how fast the machine is.
const fakeTimers = () => {
  let now = 0, next = 0;
  const due = new Map();
  globalThis.setTimeout = (fn, ms = 0) => { due.set(++next, { fn, at: now + ms }); return next; };
  globalThis.clearTimeout = (id) => { due.delete(id); };
  return (ms) => {
    now += ms;
    for (;;) {
      const ready = [...due].filter(([, t]) => t.at <= now).sort((a, b) => a[1].at - b[1].at || a[0] - b[0]);
      if (!ready.length) return;
      due.delete(ready[0][0]);
      ready[0][1].fn();
    }
  };
};
"""
