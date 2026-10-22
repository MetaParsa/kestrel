"""C3 review follow-ups: inbox trust, AI-box failure handling, transport, scorecard lookahead."""
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from ai_box.loop import AiBox, LocalTransport, claude_decider, mock_decider
from ai_box.spend import SpendCapReached, SpendLedger
from bridge.inbox import Inbox
from bridge.outbox import write_bundle
from manager import scorecard
from manager.manager import ManagerError, run_manager
from manager.mock import mock_playbook
from manager.schema import finalize
from tests.test_manager import NOW, FakeClaude, make_bundle, out, resp, text, tool_use


# ---- manager loop edge cases -------------------------------------------------------------
def test_manager_continues_after_pause_turn(tmp_path):
    fake = FakeClaude([resp("pause_turn", [text("...")]), resp("end_turn", [text(out().model_dump_json())])])
    r = run_manager(make_bundle(), fake, SpendLedger(tmp_path / "s.jsonl", 5))
    assert r.requests == 2 and fake.calls[1]["messages"][-1]["role"] == "assistant"


def test_spend_cap_hit_mid_loop(tmp_path):
    led = SpendLedger(tmp_path / "s.jsonl", cap_usd=0.36)          # first request fits, the second can't
    fake = FakeClaude([resp("tool_use", [tool_use(1, "calendar", {})]), resp("end_turn", [text("{}")])])
    with pytest.raises(SpendCapReached):
        run_manager(make_bundle(), fake, led)
    assert len(fake.calls) == 1


def test_api_timeout_is_charged_at_worst_case(tmp_path):
    class APITimeoutError(Exception):
        pass

    def boom(**kw):
        raise APITimeoutError("read timeout")
    fake = NS(beta=NS(messages=NS(create=boom)))
    led = SpendLedger(tmp_path / "s.jsonl", 5)
    with pytest.raises(ManagerError, match="APITimeoutError"):
        run_manager(make_bundle(), fake, led)
    assert led.total() > 0.3                                       # 16k output tokens at $20/M at least


def test_torn_ledger_line_counts_as_worst_case(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"cost_usd": 0.1}\n{"cost_us', encoding="utf-8")
    assert SpendLedger(p, 5).total() > 0.1 + 0.3


# ---- inbox ---------------------------------------------------------------------------
def write_pb(dirpath, pb):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"playbook-{pb.playbook_id}.json").write_text(pb.model_dump_json(), encoding="utf-8")


def test_inbox_accepts_once_and_rejects_unknown_old_stale_and_junk(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.note_issued("a" * 64, NOW)
    good = finalize(out(), bundle_hash="a" * 64, now=NOW, source="mock", model="rules-v1")
    write_pb(tmp_path / "inbox", good)
    pb, ev = inbox.poll(NOW + 10)
    assert pb.playbook_id == good.playbook_id and ev[0]["event"] == "playbook_accepted"
    assert not list((tmp_path / "inbox").glob("playbook-*.json"))

    again = finalize(out(), bundle_hash="a" * 64, now=NOW + 5, source="mock", model="m")
    unknown = finalize(out(), bundle_hash="b" * 64, now=NOW + 60, source="mock", model="m")
    inbox.note_issued("c" * 64, NOW)
    older = finalize(out(), bundle_hash="c" * 64, now=NOW - 30, source="mock", model="m")
    inbox.note_issued("d" * 64, NOW - 20 * 60)
    stale = finalize(out(), bundle_hash="d" * 64, now=NOW + 90, source="mock", model="m")
    for p in (again, unknown, older, stale):
        write_pb(tmp_path / "inbox", p)
    (tmp_path / "inbox" / "playbook-junk.json").write_text("{", encoding="utf-8")
    pb, ev = inbox.poll(NOW + 20)
    assert pb is None
    assert sorted(e["reason"] for e in ev) == sorted([
        "unknown_or_used_bundle_hash", "unknown_or_used_bundle_hash", "older_than_active",
        "bundle_too_old", "invalid: JSONDecodeError"])


def test_server_reapplies_every_bound(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.note_issued("e" * 64, NOW)
    pb = finalize(out(), bundle_hash="e" * 64, now=NOW, source="claude", model="m")
    evil = pb.model_copy(update={"regime": "range", "core_targets": {"USDTTMN": 0.9, "BTCTMN": float("nan")},
                                 "risk_multiplier": 5.0, "tactical_enabled": {"USDTTMN": True, "BTCTMN": True},
                                 "valid_until": NOW + 30 * 86400})
    (tmp_path / "inbox").mkdir(parents=True)
    import json
    (tmp_path / "inbox" / "playbook-evil.json").write_text(json.dumps(evil.model_dump()), encoding="utf-8")  # raw NaN
    got, _ = inbox.poll(NOW + 30)
    assert got.core_targets == {"USDTTMN": 0.15, "BTCTMN": 0.0}           # range bounds; NaN → safe low end
    assert got.risk_multiplier == 1.0
    assert got.valid_until == NOW + 30 + 90 * 60                          # re-based on the server clock


def test_risk_off_playbook_cannot_enable_tactical(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.note_issued("h" * 64, NOW)
    pb = finalize(out(), bundle_hash="h" * 64, now=NOW, source="claude", model="m")
    write_pb(tmp_path / "inbox", pb.model_copy(update={"regime": "risk_off"}))
    got, _ = inbox.poll(NOW + 1)
    assert got.tactical_enabled == {"USDTTMN": False, "BTCTMN": False}
    assert got.core_targets == {"USDTTMN": 0.0, "BTCTMN": 0.0}


def test_inbox_rejects_missing_or_extra_markets(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.note_issued("f" * 64, NOW)
    pb = finalize(out(), bundle_hash="f" * 64, now=NOW, source="claude", model="m")
    write_pb(tmp_path / "inbox", pb.model_copy(update={"core_targets": {"USDTTMN": 0.1, "ETHTMN": 0.1}}))
    got, ev = inbox.poll(NOW + 1)
    assert got is None and ev[0]["reason"].startswith("invalid: core_targets")


def test_laptop_clock_skew_is_logged_not_fatal(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.note_issued("g" * 64, NOW)
    write_pb(tmp_path / "inbox", finalize(out(), bundle_hash="g" * 64, now=NOW + 600, source="mock", model="m"))
    got, ev = inbox.poll(NOW + 5)
    assert got is not None and ev[0]["clock_skew_s"] == pytest.approx(595)
    assert got.valid_until == NOW + 5 + 60 * 60


def test_processed_name_clash_does_not_crash(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    (tmp_path / "inbox" / "rejected").mkdir(parents=True)
    (tmp_path / "inbox" / "rejected" / "playbook-x.json").write_text("old", encoding="utf-8")
    (tmp_path / "inbox" / "playbook-x.json").write_text("{", encoding="utf-8")
    _, ev = inbox.poll(NOW)
    assert ev[0]["event"] == "playbook_rejected"
    assert len(list((tmp_path / "inbox" / "rejected").iterdir())) == 2


# ---- laptop AI box ---------------------------------------------------------------------
def box_with(tmp_path, decide, transport=None, **kw):
    run = tmp_path / "run"
    events = []
    box = AiBox(transport or LocalTransport(run), decide, local_dir=tmp_path / "box", log=events.append,
                clock=kw.pop("clock", lambda: NOW + 30), **kw)
    return run, box, events


def test_ai_box_round_trip_with_mock_manager(tmp_path):
    run, box, _ = box_with(tmp_path, mock_decider)
    b = make_bundle()
    write_bundle(b, run / "outbox")
    inbox = Inbox(run / "inbox")
    inbox.note_issued(b["hash"], b["created_at"])
    pb = box.cycle()
    assert pb is not None and pb.source == "mock" and pb.bundle_hash == b["hash"]
    accepted, _ = inbox.poll(NOW + 60)
    assert accepted.playbook_id == pb.playbook_id


def test_one_decision_per_request_even_when_the_bundle_is_rebuilt(tmp_path):
    calls = []

    def decide(bundle):
        calls.append(bundle["hash"])
        return mock_decider(bundle)
    run, box, _ = box_with(tmp_path, decide)
    write_bundle(make_bundle(request_id="req-7"), run / "outbox")
    box.cycle()
    write_bundle(make_bundle(request_id="req-7", reason="scheduled-rebuild"), run / "outbox")
    box.cycle()
    assert len(calls) == 1
    write_bundle(make_bundle(request_id="req-8"), run / "outbox")
    box.cycle()
    assert len(calls) == 2


def test_failed_push_retries_the_push_not_the_paid_call(tmp_path):
    calls = []

    def decide(bundle):
        calls.append(1)
        return mock_decider(bundle)

    class Flaky(LocalTransport):
        fails = 1

        def push_playbook(self, src):
            if self.fails:
                self.fails -= 1
                return False
            return super().push_playbook(src)
    run, box, events = box_with(tmp_path, decide, transport=Flaky(tmp_path / "run"))
    write_bundle(make_bundle(), run / "outbox")
    box.cycle()
    box.cycle()
    assert len(calls) == 1 and events[-1]["event"] == "playbook_pushed_on_retry"
    assert len(list((run / "inbox").glob("playbook-*.json"))) == 1


def test_api_errors_never_kill_the_box(tmp_path):
    def decide(bundle):
        raise ConnectionError("VPN dropped")
    run, box, events = box_with(tmp_path, decide)
    write_bundle(make_bundle(), run / "outbox")
    assert box.cycle() is None and events[-1]["event"] == "manager_failed"
    assert box.cycle() is None
    assert box.cycle() is None and events[-1]["event"] == "request_given_up"


def test_spend_cap_falls_back_to_rules(tmp_path):
    def decide(bundle):
        raise SpendCapReached("cap")
    run, box, events = box_with(tmp_path, decide)
    write_bundle(make_bundle(), run / "outbox")
    pb = box.cycle()
    assert pb.source == "mock" and any(e["event"] == "spend_cap_fallback_rules" for e in events)


def test_stale_bundle_and_min_gap(tmp_path):
    clock = {"t": NOW + 11 * 60}
    run, box, events = box_with(tmp_path, mock_decider, clock=lambda: clock["t"], min_gap_s=1200)
    write_bundle(make_bundle(), run / "outbox")
    assert box.cycle() is None and events[-1]["event"] == "bundle_stale"
    clock["t"] = NOW + 30
    assert box.cycle() is not None
    write_bundle(make_bundle(request_id="req-2"), run / "outbox")
    assert box.cycle() is None                                   # 20-minute gap not over
    write_bundle(make_bundle(request_id="req-3", reason="event: 1h move 3 sigma"), run / "outbox")
    assert box.cycle() is not None                               # events skip the gap


def test_tampered_and_unreadable_bundles(tmp_path):
    run, box, events = box_with(tmp_path, mock_decider)
    write_bundle(dict(make_bundle(), reason="tampered"), run / "outbox")
    assert box.cycle() is None and events[-1]["event"] == "bundle_hash_mismatch"
    (run / "outbox" / "bundle.json").write_text("[1, 2]", encoding="utf-8")
    assert box.cycle() is None and events[-1]["event"] == "bundle_unreadable"
    write_bundle(make_bundle(manager_due=False), run / "outbox")
    assert box.cycle() is None


def test_ai_box_with_fake_claude_records_cost_and_transcript(tmp_path):
    fake = FakeClaude([resp("end_turn", [text(out().model_dump_json())])])
    run, box, _ = box_with(tmp_path, claude_decider(fake, SpendLedger(tmp_path / "s.jsonl", 5)))
    write_bundle(make_bundle(), run / "outbox")
    pb = box.cycle()
    assert pb.source == "claude" and pb.cost_usd > 0
    assert list((tmp_path / "box").glob("transcript-*.json"))


def test_scp_transport_uses_relative_local_paths_and_temp_rename(tmp_path, monkeypatch):
    from ai_box import loop
    seen = []

    def fake_run(cmd, cwd=None, timeout=None, capture_output=None):
        seen.append((cmd, cwd))
        return NS(returncode=0)
    monkeypatch.setattr(loop.subprocess, "run", fake_run)
    t = loop.ScpTransport("trader@1.2.3.4", "/opt/wa/run")
    f = tmp_path / "playbook-x.json"
    f.write_text("{}", encoding="utf-8")
    assert t.push_playbook(f) and t.pull_bundle(tmp_path / "bundle.json")
    (scp_up, cwd1), (ssh_mv, _), (scp_down, cwd2) = seen
    assert scp_up[-2:] == ["playbook-x.json", "trader@1.2.3.4:/opt/wa/run/inbox/.playbook-x.json.tmp"]
    assert cwd1 == tmp_path and cwd2 == tmp_path and scp_down[-1] == "bundle.json"
    assert "mv -f" in ssh_mv[-1] and "ServerAliveInterval=10" in scp_up


def test_event_shock_mock_holds_current_core():
    b = make_bundle(portfolio={"core_exposure": {"USDTTMN": 0.18, "BTCTMN": 0.04}})
    b["markets"]["USDTTMN"]["stats"]["ret_1h"] = 0.05
    pb = mock_playbook(b)
    assert pb.regime == "event_shock" and pb.core_targets.model_dump() == {"USDTTMN": 0.18, "BTCTMN": 0.04}


# ---- forecast scorecard -------------------------------------------------------------------
def test_scorecard_hits_without_lookahead():
    idx = pd.date_range("2026-10-20", periods=72, freq="1h", tz="UTC")
    c = np.r_[np.full(30, 100.0), np.linspace(100, 104, 42)]
    b = pd.DataFrame({"c": c}, index=idx)
    t0 = (idx[40] + pd.Timedelta(minutes=17)).timestamp()               # off the hour
    pbs = [{"playbook_id": "p1", "created_at": t0,
            "forecast_4h": {"USDTTMN": {"direction": "up", "vol": "normal"}}}]
    s = scorecard.score(pbs, {"USDTTMN": b}, now=idx[-1].timestamp())
    assert s["n"] == 1 and s["direction_hit_rate"] == 1.0
    assert scorecard.score(pbs, {"USDTTMN": b}, now=t0 + 4.5 * 3600)["n"] == 0


def test_scorecard_uses_only_closes_known_at_the_time():
    idx = pd.date_range("2026-10-20", periods=20, freq="1h", tz="UTC")
    c = np.full(20, 100.0)
    c[10] = 110.0                       # the bar OPENING at t0 jumps — not known at t0
    b = pd.DataFrame({"c": c}, index=idx)
    t0 = idx[10].timestamp()
    rows = scorecard.score([{"created_at": t0, "forecast_4h": {"USDTTMN": {"direction": "flat", "vol": "normal"}}}],
                           {"USDTTMN": b}, now=idx[-1].timestamp())["recent"]
    assert rows[0]["baseline"] == "flat"
