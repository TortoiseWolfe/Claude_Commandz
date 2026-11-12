#!/usr/bin/env python3
"""Runs one Bash tool call and masks secret values in its stdout and stderr, line by line, as the lines complete.
Use when: shell_scrub_prefix.sh hands over a Bash tool call (it is called as: shell_scrub_wrapper.py '<whole command line>').

What it does: touches the heartbeat file, starts `bash -c "$1"` as a child in the same process group with stdin inherited
and stdout/stderr as pipes, forwards SIGINT/SIGTERM/SIGHUP/SIGQUIT to the child, and pumps both pipes in one thread
(selectors), each through its own secret_scrubber.LineFilter at tier 2. Exit code = the child's (128+n for a signal).
A detached grandchild (nohup server &) that keeps a pipe open: once the child has exited and a pipe stays quiet for 0.15 s,
the held partial lines are written out and a detached continuation (new session) keeps filtering until end of file; the
parent exits at once. When the downstream pipe breaks the continuation keeps draining, so the background process never
dies from a closed pipe.
Fails closed: a filter error prints the withheld notice instead of the text and the pipes keep draining; if secret_scrubber
cannot be imported every output line becomes a withheld notice naming the off switch file. Never raw output.
Environment (tests): SHELL_SCRUB_HEARTBEAT (default ~/.claude/state/shell-scrub-heartbeat), SHELL_SCRUB_OFF_FILE (only named in
the notice), SHELL_SCRUB_LIB_DIR (where secret_scrubber is imported from, default beside this file).
"""
import os
import selectors
import signal
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
STATE = os.path.join(os.path.expanduser("~"), ".claude", "state")
HEARTBEAT = os.environ.get("SHELL_SCRUB_HEARTBEAT") or os.path.join(STATE, "shell-scrub-heartbeat")
OFF_FILE = os.environ.get("SHELL_SCRUB_OFF_FILE") or os.path.join(STATE, "shell-scrub-off")
LIB_DIR = os.environ.get("SHELL_SCRUB_LIB_DIR") or HERE
QUIET = 0.15
CHUNK = 65536
MAX_HELD = 1024 * 1024
FORWARD = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)


class Withholder:
    """Same interface as LineFilter: every line of the stream becomes the notice; the text itself is never kept."""

    def __init__(self, notice):
        self.notice = notice.encode()
        self.held = 0

    def feed(self, data):
        out = []
        for part in data.split(b"\n")[:-1]:
            out.append(self.notice)
            self.held = 0
        self.held += len(data) - (data.rfind(b"\n") + 1)
        if self.held > MAX_HELD:
            self.held = 0
            out.append(self.notice)
        return b"".join(out)

    def flush_partial(self):
        if not self.held:
            return b""
        self.held = 0
        return self.notice

    finish = flush_partial


class Sink:
    def __init__(self, fd):
        self.fd = fd
        self.dead = False

    def write(self, data):
        while data and not self.dead:
            try:
                n = os.write(self.fd, data)
                data = data[n:]
            except BlockingIOError:
                time.sleep(0.005)
            except OSError:
                self.dead = True      # downstream closed: keep reading the child's pipe, drop the text


class Stream:
    def __init__(self, read_fd, out_fd, filt):
        self.rfd = read_fd
        self.sink = Sink(out_fd)
        self.filt = filt


def touch_heartbeat():
    try:
        os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
        with open(HEARTBEAT, "a"):
            pass
        os.utime(HEARTBEAT, None)
    except OSError:
        pass


def load_filters():
    """(make_filter, load_error). Never raises."""
    sys.path[:] = [LIB_DIR] + [p for p in sys.path if os.path.realpath(p or ".") != HERE]
    try:
        import secret_scrubber as ss
        ss.LineFilter(2)
        return (lambda: ss.LineFilter(2)), None
    except BaseException as exc:    # noqa: BLE001 - anything: stay closed
        return None, type(exc).__name__


def exit_code(status):
    code = os.waitstatus_to_exitcode(status)
    return code if code >= 0 else 128 - code


def feed(stream, data, final=False):
    """Filter data and write it. A filter error replaces the stream's filter by a Withholder."""
    try:
        out = stream.filt.finish() if final else stream.filt.feed(data)
    except BaseException:    # noqa: BLE001
        stream.filt = Withholder(GENERIC_NOTICE)
        out = stream.filt.notice
    stream.sink.write(out)


GENERIC_NOTICE = "[withheld: the output filter failed on this text]\n"


def pump(sel, streams, wake, state, detached):
    """Read until every pipe is at end of file. Returns "eof", or "quiet" when the child has exited and a pipe stayed
    silent for QUIET seconds (only when not already detached)."""
    while streams:
        if not detached and state["rc"] is None:
            reap(state)
            if state["rc"] is not None and wake is not None:
                stop_wake(sel, wake)
                wake = None
        timeout = QUIET if (not detached and state["rc"] is not None) else None
        events = sel.select(timeout)
        if not events and timeout is not None:
            return "quiet"
        for key, _ in events:
            if key.data is None:
                try:
                    os.read(key.fd, 4096)
                except OSError:
                    pass
                continue
            st = key.data
            try:
                data = os.read(st.rfd, CHUNK)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if data:
                feed(st, data)
            else:
                feed(st, b"", final=True)
                sel.unregister(st.rfd)
                os.close(st.rfd)
                streams.remove(st)
    return "eof"


def reap(state):
    try:
        pid, status = os.waitpid(state["pid"], os.WNOHANG)
    except ChildProcessError:
        state["rc"] = 0
        return
    if pid:
        state["rc"] = exit_code(status)


def stop_wake(sel, wake):
    try:
        signal.set_wakeup_fd(-1)
        sel.unregister(wake[0])
    except (OSError, ValueError, KeyError):
        pass


def main():
    if len(sys.argv) < 2:
        return 0
    cmd = sys.argv[1]
    touch_heartbeat()
    make_filter, err = load_filters()
    if make_filter is None:
        notice = ("[withheld: the output filter could not load (%s), so this line is not shown; to run commands "
                  "unfiltered create the file %s]\n" % (err, OFF_FILE))
        mk = lambda: Withholder(notice)    # noqa: E731
    else:
        mk = make_filter
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    wr, ww = os.pipe()
    os.set_blocking(wr, False)
    os.set_blocking(ww, False)
    state = {"pid": None, "rc": None}

    def forward(signum, _frame):
        if state["pid"] and state["rc"] is None:
            try:
                os.kill(state["pid"], signum)
            except OSError:
                pass
    for s in FORWARD:
        signal.signal(s, forward)
    signal.signal(signal.SIGCHLD, lambda *_: None)
    signal.set_wakeup_fd(ww, warn_on_full_buffer=False)
    try:
        state["pid"] = os.posix_spawnp(
            "bash", ["bash", "-c", cmd], os.environ,
            file_actions=[(os.POSIX_SPAWN_DUP2, w1, 1), (os.POSIX_SPAWN_DUP2, w2, 2)],
            setsigdef=[signal.SIGPIPE, signal.SIGXFSZ])
    except OSError as exc:
        sys.stderr.write("shell-scrub: could not start bash (%s)\n" % exc)
        return 127
    os.close(w1)
    os.close(w2)
    streams = [Stream(r1, 1, mk()), Stream(r2, 2, mk())]
    sel = selectors.DefaultSelector()
    for st in streams:
        os.set_blocking(st.rfd, False)
        sel.register(st.rfd, selectors.EVENT_READ, st)
    wake = (wr, ww)
    sel.register(wr, selectors.EVENT_READ, None)
    result = pump(sel, streams, wake, state, False)
    if result == "eof":
        if state["rc"] is None:
            state["rc"] = exit_code(os.waitpid(state["pid"], 0)[1])
        return state["rc"]
    # The child has exited but a detached process still holds a pipe.
    for st in streams:
        try:
            held = st.filt.flush_partial()
        except BaseException:    # noqa: BLE001
            st.filt = Withholder(GENERIC_NOTICE)
            held = b""
        st.sink.write(held)
    if os.fork() != 0:
        os._exit(state["rc"])
    # continuation: own session, no stdin, ignores hangup and interrupt, keeps filtering until end of file
    try:
        os.setsid()
        fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(fd, 0)
        os.close(fd)
    except OSError:
        pass
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)
    pump(sel, streams, None, state, True)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
