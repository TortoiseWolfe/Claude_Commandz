"""Tests for coop_call_actions.py: the checker keeps only quotes that are in the transcript word for word, the tool talks only to
a local Ollama, and the whole flow (stub server, retries, skipped window, mode 600, no quote text on stdout) holds.

The test text is invented. No real call is read."""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
sys.path.insert(0, SCRIPTS)
import coop_call_actions as cca  # noqa: E402

TOOL = os.path.join(SCRIPTS, "coop_call_actions.py")


def test_whole_flow_selftest_passes():
    r = subprocess.run([sys.executable, "-I", TOOL, "--selftest"], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST OK" in r.stdout


def test_normalise_ignores_case_punctuation_and_spacing():
    assert cca.normalise("  Don't   STOP, now!  ") == "don t stop now"


def test_srt_cues_and_windows_overlap_with_whole_cues():
    srt = "".join("%d\n00:00:%02d,000 --> 00:00:%02d,500\ncue number %d has some words in it\n\n" % (i + 1, i, i, i) for i in range(20))
    cues = cca.parse_srt(srt)
    assert len(cues) == 20 and cues[3]["start"] == 3.0
    wins = cca.build_windows(cues, 120, 60)
    assert wins[0][0] == 0 and wins[-1][1] == 20
    for (a, b), (c, d) in zip(wins, wins[1:]):
        assert c > a and c < b  # moves on, and overlaps the window before


def test_checker_keeps_true_drops_paraphrase_and_short():
    cues = cca.parse_srt("1\n00:00:00,000 --> 00:00:04,000\nso the backup copies get deleted on friday\n\n"
                         "2\n00:00:05,000 --> 00:00:09,000\nGreg will send the policy draft to everybody tomorrow\n\n")
    win = cca.Joined(cues)
    counts = {"bad_shape": 0, "too_short": 0, "not_in_window": 0, "not_in_transcript": 0, "duplicate": 0}
    items = [
        {"kind": "owes", "quote": "Greg will send the policy draft to everybody tomorrow."},
        {"kind": "owes", "quote": "Greg promised to send the draft to the whole team"},
        {"kind": "owes", "quote": "Greg will send"},
        {"kind": "nonsense", "quote": "Greg will send the policy draft to everybody tomorrow"},
    ]
    kept = cca.check_items(items, win, win.padded, None, counts)
    assert len(kept) == 1 and kept[0]["start"] == 5.0
    assert counts["not_in_window"] == 1 and counts["too_short"] == 1 and counts["bad_shape"] == 1


@pytest.mark.parametrize("url", ["http://192.0.2.1:11434", "https://127.0.0.1:11434", "http://127.0.0.1@evil.example", "http://example.com"])
def test_non_local_urls_are_refused(url):
    with pytest.raises(SystemExit):
        cca.check_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434"])
def test_local_urls_pass(url):
    assert cca.check_url(url) == url


# ---------------------------------------------------------------- the clean step (invented text only; no Ollama)

import re  # noqa: E402


def make_srt(texts, step=5):
    return "\n".join("%d\n00:%02d:%02d,000 --> 00:%02d:%02d,500\n%s\n" % (
        i + 1, (i * step) // 60, (i * step) % 60, (i * step + 3) // 60, (i * step + 3) % 60, t) for i, t in enumerate(texts))


def numbered(text):
    """{n: raw} from the numbered text the clean step sends."""
    return {int(m.group(1)): m.group(2) for m in (re.match(r"^(\d+): (.*)$", ln) for ln in text.splitlines()) if m}


CLEAN_RAW = [
    "um so we should uh book the Quillmarsh venue",         # 0 filler
    "the the Fennick budget is ready, Dara",                # 1 stutter
    "we could maybe go no wait the Fennick review is friday",  # 2 false start
    "pass the Zorble salt please",                          # 3 the trap
    "send the Morvane notes",                               # 4 plain
    "all done here",                                        # 5 plain
]
CLEAN_ANSWER = {
    0: "so we should book the Quillmarsh venue",
    1: "the Fennick budget is ready, Dara",
    2: "the Fennick review is friday",
    3: "pass the Zorble salt please now",                   # the added word
    4: "send the Morvane notes",
    5: "all done here",
}


def cues_of(texts):
    return cca.parse_srt(make_srt(texts))


def test_a_subset_keep_removes_filler_and_stutter_and_keeps_raw_spelling():
    assert cca.subset_keep("So um we, uh, Agreed.", "so we agreed") == "So we, Agreed."
    assert cca.subset_keep("my my plan", "my plan") == "my plan"
    assert cca.subset_keep("Ok, Dara said: go.", "dara said go") == "Dara said: go."
    assert cca.subset_keep("we agreed", "we all agreed") is None       # an added word
    assert cca.subset_keep("we agreed today", "today we agreed") is None  # reordered
    assert cca.subset_keep("we agreed", "") is None
    assert cca.subset_keep("we agreed", "  ,, ") is None
    assert cca.word_count("So um, we agreed.") == 4


def test_b_clean_cues_trap_kept_raw_and_words_add_up(monkeypatch):
    cues = cues_of(CLEAN_RAW)
    monkeypatch.setattr(cca, "ask_clean_window", lambda base, model, text, timeout: {n: CLEAN_ANSWER[n] for n in numbered(text)})
    lines, st = cca.clean_cues("http://127.0.0.1:1", "m", cues, 5000, 0, 5)
    assert len(lines) == 6
    assert [ln[:10] for ln in lines] == ["[00:00:00]", "[00:00:05]", "[00:00:10]", "[00:00:15]", "[00:00:20]", "[00:00:25]"]
    assert lines[3].endswith("pass the Zorble salt please") and "now" not in lines[3]
    assert lines[0].endswith("so we should book the Quillmarsh venue")
    assert lines[2].endswith("the Fennick review is friday")
    assert lines[1].endswith("the Fennick budget is ready, Dara")
    assert st["cues_kept_raw"] == 1 and st["cues_failing_subset"] == 1
    assert st["words_kept"] + st["words_dropped"] == sum(cca.word_count(t) for t in CLEAN_RAW)
    assert st["words_dropped"] > 0 and st["windows_read"] == 1 and st["windows_skipped"] == 0


def test_c_missing_empty_and_failed_window_keep_raw(monkeypatch):
    cues = cues_of(CLEAN_RAW)

    def fake(base, model, text, timeout):
        ans = numbered(text)
        ans.pop(1, None)       # not answered
        ans[4] = ""            # cleaned to nothing
        return ans
    monkeypatch.setattr(cca, "ask_clean_window", fake)
    lines, st = cca.clean_cues("http://127.0.0.1:1", "m", cues, 5000, 0, 5)
    assert lines[1].endswith(CLEAN_RAW[1]) and lines[4].endswith(CLEAN_RAW[4])
    assert st["cues_kept_raw"] == 2 and st["cues_failing_subset"] == 1  # only the empty answer failed the check

    wins = cca.build_windows(cues, 90, 0)
    assert len(wins) == 2

    def fail_second(base, model, text, timeout):
        if 0 not in numbered(text):
            raise ValueError("bad")
        return numbered(text)
    monkeypatch.setattr(cca, "ask_clean_window", fail_second)
    lines, st = cca.clean_cues("http://127.0.0.1:1", "m", cues, 90, 0, 5)
    assert st["windows_skipped"] == 1 and st["windows_read"] == 1
    assert len(lines) == 6 and all(lines[i].endswith(CLEAN_RAW[i]) for i in range(wins[1][0], 6))
    assert st["cues_kept_raw"] == 6 - wins[1][0]


def test_d_overlap_cues_are_decided_once_by_the_first_window(monkeypatch):
    texts = ["um cue number %d has some words in it" % i for i in range(12)]
    cues = cues_of(texts)
    wins = cca.build_windows(cues, 130, 70)
    assert len(wins) > 2 and wins[1][0] < wins[0][1]
    calls = []

    def fake(base, model, text, timeout):
        calls.append(1)
        ans = numbered(text)
        if len(calls) == 1:  # the first window drops the filler
            return {n: t.replace("um ", "", 1) for n, t in ans.items()}
        return {n: t.split()[0] for n, t in ans.items()}  # any later answer would cut the line to one word
    monkeypatch.setattr(cca, "ask_clean_window", fake)
    lines, st = cca.clean_cues("http://127.0.0.1:1", "m", cues, 130, 70, 5)
    assert len(lines) == len(cues) == st["cues"]
    for i in range(wins[0][0], wins[0][1]):
        assert lines[i].endswith("cue number %d has some words in it" % i)
    assert len(calls) == len(wins)


def test_e_write_clean_mode_previous_and_inputs_untouched(tmp_path):
    (tmp_path / "transcript.srt").write_text(make_srt(CLEAN_RAW))
    (tmp_path / "transcript.md").write_text("# Call\n\n[00:00:00] um so we\n")
    before = [(tmp_path / n).read_bytes() for n in ("transcript.srt", "transcript.md")]
    p = cca.write_clean(tmp_path, ["[00:00:00] one", "[00:00:05] two"])
    assert p.read_text() == "[00:00:00] one\n[00:00:05] two\n" and (p.stat().st_mode & 0o777) == 0o600
    cca.write_clean(tmp_path, ["[00:00:00] three"])
    prev = tmp_path / "transcript-clean.previous.md"
    assert prev.read_text() == "[00:00:00] one\n[00:00:05] two\n" and (prev.stat().st_mode & 0o777) == 0o600
    assert (tmp_path / "transcript-clean.md").read_text() == "[00:00:00] three\n"
    assert before == [(tmp_path / n).read_bytes() for n in ("transcript.srt", "transcript.md")]


def test_f_default_mode_cleans_first_then_reads_clean(tmp_path, monkeypatch):
    texts = ["um so the Tessolin ledger is late", "the the Tessolin ledger moves to january", "all done for today then"]
    (tmp_path / "transcript.srt").write_text(make_srt(texts))
    clean_calls, read_texts = [], []

    def fake_clean(base, model, text, timeout):
        clean_calls.append(1)
        return {n: re.sub(r"\b(um|the the)\b ?", lambda m: "the " if m.group(1) == "the the" else "", t, count=1) for n, t in numbered(text).items()}
    monkeypatch.setattr(cca, "ask_clean_window", fake_clean)
    monkeypatch.setattr(cca, "check_server", lambda *a: None)
    monkeypatch.setattr(cca, "ask_window", lambda base, model, text, timeout: read_texts.append(text) or [])
    d = str(tmp_path)

    assert cca.main(["--dir", d]) == 0
    clean = tmp_path / "transcript-clean.md"
    assert clean.is_file() and len(clean_calls) == 1
    assert "um" not in " ".join(read_texts).split() and "so the Tessolin ledger is late" in " ".join(read_texts)
    rep = (tmp_path / "report.md").read_text()
    assert "reading: transcript-clean.md" in rep and "clean: cues 3" in rep

    assert cca.main(["--dir", d]) == 0            # fresh: not rebuilt
    assert len(clean_calls) == 1
    srt_m = (tmp_path / "transcript.srt").stat().st_mtime
    os.utime(clean, (srt_m - 100, srt_m - 100))   # stale: older than the srt
    assert cca.main(["--dir", d]) == 0
    assert len(clean_calls) == 2

    os.remove(clean)
    os.remove(tmp_path / "transcript-clean.previous.md")
    assert cca.main(["--dir", d, "--raw"]) == 0   # --raw never makes a clean file
    assert not clean.exists() and len(clean_calls) == 2


def test_f2_raw_run_prints_the_raw_line(tmp_path, monkeypatch, capsys):
    (tmp_path / "transcript.srt").write_text(make_srt(["um so the Tessolin ledger is late"]))
    monkeypatch.setattr(cca, "check_server", lambda *a: None)
    monkeypatch.setattr(cca, "ask_window", lambda *a: [])
    assert cca.main(["--dir", str(tmp_path), "--raw"]) == 0
    out = capsys.readouterr().out
    assert "RAW: reading the raw captions (--raw), not the cleaned file" in out
    assert "reading: RAW captions (--raw)" in (tmp_path / "report.md").read_text()


def test_g_quotes_checked_against_the_clean_text(tmp_path):
    clean = cca.parse_clean_md("[00:00:00] so we agreed on the Brindle budget\n[00:00:05] Dara sends the Brindle plan to everyone\n")
    assert [c["start"] for c in clean] == [0.0, 5.0]
    whole = cca.Joined(clean)
    counts = {"bad_shape": 0, "too_short": 0, "not_in_window": 0, "not_in_transcript": 0, "not_in_raw": 0, "duplicate": 0}
    raw_only = {"kind": "decision", "quote": "so um we agreed on the Brindle budget"}
    spans = {"kind": "decision", "quote": "so we agreed on the Brindle budget"}
    kept = cca.check_items([raw_only, spans], whole, whole.padded, None, counts, {0, 5})
    assert len(kept) == 1 and kept[0]["start"] == 0.0 and counts["not_in_window"] == 1
    # the same quote, but the raw captions have no cue at second 0: dropped and counted
    counts2 = dict.fromkeys(counts, 0)
    assert cca.check_items([spans], whole, whole.padded, None, counts2, {5, 9}) == [] and counts2["not_in_raw"] == 1


def test_h_md_false_alarm_is_the_time_stamps(tmp_path):
    words = ["alder", "bramble", "cinder", "dorrin", "ember", "fallow", "garnet", "hollin", "ivory", "juniper", "kestrel", "lumen"]
    texts = []
    for i in range(400):
        k = 3 + i % 4
        texts.append(" ".join(words[(i * 5 + j * 7 + i // 12) % 12] + str((i + j) % 9) for j in range(k)))
    cues = cues_of(texts)
    assert len(cues) == 400
    md = "# Call\n\nSource: invented\n\n" + "\n".join("[%s] %s" % (cca.fmt_time(c["start"]), c["text"]) for c in cues) + "\n"
    srt_norm = cca.Joined(cues).text
    assert cca.md_matches_srt(cca.normalise(md), srt_norm) is False          # the old path: digits break the runs
    assert cca.md_matches_srt(cca.normalise(cca.strip_md_times(md)), srt_norm) is True


def test_i_compare_counts_only(tmp_path, capsys):
    (tmp_path / "transcript.srt").write_text(make_srt(["alpha one two", "bravo three four", "charlie five six", "delta seven eight"]))
    (tmp_path / "transcript.md").write_text("# Call\n\n[00:00:00] alpha one two\n[00:00:05] bravo three NINE\n[00:00:15] delta seven eight\n[00:00:40] echo extra line\n")
    assert cca.main(["--dir", str(tmp_path), "--compare"]) == 0
    out = capsys.readouterr().out
    assert out == "compare: srt cues 4, md cues 4, same 2, differing 1, missing 1, extra 1\n"
    for w in ("alpha", "bravo", "charlie", "echo"):
        assert w not in out


def test_j_nothing_prints_call_text_and_reports_are_private(tmp_path, monkeypatch, capsys):
    (tmp_path / "transcript.srt").write_text(make_srt(CLEAN_RAW))
    monkeypatch.setattr(cca, "ask_clean_window", lambda base, model, text, timeout: {n: CLEAN_ANSWER[n] for n in numbered(text)})
    monkeypatch.setattr(cca, "check_server", lambda *a: None)
    monkeypatch.setattr(cca, "ask_window", lambda *a: [{"kind": "decision", "quote": "pass the Zorble salt please"}])
    assert cca.main(["--dir", str(tmp_path), "--clean"]) == 0
    assert (tmp_path / "clean-report.md").read_text().startswith("DONE clean cues 6")
    assert cca.main(["--dir", str(tmp_path)]) == 0
    cap = capsys.readouterr()
    for w in ("Quillmarsh", "Fennick", "Zorble", "Morvane", "salt", "venue"):
        assert w not in cap.out + cap.err
    for n in ("clean-report.md", "report.md", "actions.md", "transcript-clean.md"):
        assert ((tmp_path / n).stat().st_mode & 0o777) == 0o600


def test_k_clean_refuses_a_non_local_url(tmp_path):
    (tmp_path / "transcript.srt").write_text(make_srt(CLEAN_RAW))
    with pytest.raises(SystemExit):
        cca.main(["--dir", str(tmp_path), "--clean", "--url", "http://192.0.2.1:11434"])
