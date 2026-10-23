"""C3: bundle → (mock | fake Claude) → playbook → inbox. No network, no paid calls."""
import json
from datetime import datetime
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

import config
from ai_box.loop import AiBox, LocalTransport, claude_decider, mock_decider
from ai_box.spend import SpendCapReached, SpendLedger, Usage, cost_usd
from bridge.inbox import Inbox
from bridge.outbox import build_bundle, market_section, write_bundle
from data import bars
from data.poll import MarketSnapshot
from manager import scorecard
from manager.manager import PLAYBOOK_SCHEMA, ManagerError, run_manager
from manager.mock import mock_playbook
from manager.schema import PerMarketBool, PerMarketFloat, PlaybookOut, content_hash, finalize
from manager.tools import TOOLS, run_tool

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=config.TEHRAN).timestamp()


def grid(start_px, drift, seed, minutes=3 * 1440):
    rng = np.random.default_rng(seed)
    t0 = int(NOW // 60 * 60) - minutes * 60
    c = start_px * np.exp(np.cumsum(rng.normal(drift, 0.0005, minutes)))
    df = pd.DataFrame({"t": t0 + 60 * np.arange(minutes), "o": c, "h": c * 1.0002, "l": c * 0.9998, "c": c,
                       "v": rng.integers(0, 3, minutes).astype(float)})
    return bars.to_minute_grid(df)


def make_bundle(manager_due=True, usd_drift=0.0, btc_drift=0.0, request_id="req-1", reason="scheduled",
                portfolio=None):
    snap = lambda m, px: MarketSnapshot(m, px * 0.9998, px * 1.0002, NOW - 5, 0.1, px, NOW - 30)
    g = {"USDTTMN": grid(266_000, usd_drift, 1), "BTCTMN": grid(28e9, btc_drift, 2)}
    markets = {m: market_section(g[m], snap(m, float(g[m]["c"].iloc[-1])), NOW) for m in g}
    return build_bundle(now=NOW, markets=markets,
                        portfolio=portfolio or {"equity": 5_000_000, "gain": 0.0, "mode": "normal",
                                                   "core_exposure": {"USDTTMN": 0.0, "BTCTMN": 0.0}},
                        recent_decisions=[{"decision_id": "d1", "decided_by": "manager"}],
                        previous_playbooks=[], forecast_scorecard={"n": 0}, tactical={"live": False},
                        manager_due=manager_due, reason=reason,
                        manager_request_id=request_id)


# ---- bundle ---------------------------------------------------------------------
def test_bundle_is_hashed_and_compact(tmp_path):
    b = make_bundle()
    assert content_hash({k: v for k, v in b.items() if k != "hash"}) == b["hash"]
    assert len(b["markets"]["USDTTMN"]["bars_1h"]) == 48 and len(b["markets"]["BTCTMN"]["bars_5m"]) == 24
    assert b["calendar"]["weekday_en"] == "Thursday" and b["calendar"]["quiet_day"]
    p = write_bundle(b, tmp_path)
    assert json.loads(p.read_text(encoding="utf-8"))["hash"] == b["hash"]
    assert len(p.read_text(encoding="utf-8")) < 40_000        # ≈ 10k tokens worst case


# ---- tools ------------------------------------------------------------------------
def test_tools_are_strict_and_read_only():
    for t in TOOLS:
        assert t["strict"] and t["input_schema"]["additionalProperties"] is False
        assert not any(w in t["name"] for w in ("order", "place", "cancel", "trade"))


def test_tool_outputs():
    b = make_bundle()
    assert json.loads(run_tool("market_brief", {"market": "USDTTMN"}, b))["stats"]["last"] > 0
    assert len(json.loads(run_tool("candles", {"market": "BTCTMN", "tf": "1h", "n": 500}, b))) == 48
    assert len(json.loads(run_tool("candles", {"market": "BTCTMN", "tf": "5m", "n": 3}, b))) == 3
    assert json.loads(run_tool("calendar", {}, b))["hours_to_end"] > 0
    with pytest.raises(KeyError):
        run_tool("place_order", {}, b)


# ---- schema / clamps --------------------------------------------------------------
def out(**kw):
    base = dict(regime="devaluation_drift", core_targets=PerMarketFloat(USDTTMN=0.2, BTCTMN=0.1),
                tactical_enabled=PerMarketBool(USDTTMN=True, BTCTMN=False), risk_multiplier=1.0, valid_minutes=60,
                forecast_4h={"USDTTMN": {"direction": "up", "vol": "normal"},
                             "BTCTMN": {"direction": "flat", "vol": "low"}},
                text_inputs_used=["Thursday"], signals_cited=["x"], rationale_fa="تست")
    base.update(kw)
    return PlaybookOut.model_validate(base)


def test_finalize_clamps_everything_that_could_loosen_a_limit():
    o = out(regime="risk_off", core_targets=PerMarketFloat(USDTTMN=0.6, BTCTMN=0.3), risk_multiplier=4,
            valid_minutes=600)
    pb = finalize(o, bundle_hash="h" * 64, now=NOW, source="claude", model="m")
    assert pb.core_targets == {"USDTTMN": 0.0, "BTCTMN": 0.0}
    assert pb.tactical_enabled == {"USDTTMN": False, "BTCTMN": False}
    assert pb.risk_multiplier == 1.0 and pb.valid_until - pb.created_at == 90 * 60
    assert len(pb.clamps) == 5


def test_output_schema_is_self_contained_and_closed():
    s = json.dumps(PLAYBOOK_SCHEMA)
    assert "$ref" not in s and "$defs" not in s

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)
    assert all(o.get("additionalProperties") is False for o in objects(PLAYBOOK_SCHEMA))


def test_mock_manager_is_deterministic_and_reacts_to_drift():
    flat, drift = make_bundle(), make_bundle(usd_drift=0.00002)
    assert mock_playbook(flat) == mock_playbook(flat)
    assert mock_playbook(drift).regime == "devaluation_drift"


# ---- spend ledger -------------------------------------------------------------------
def test_cost_math_and_cap(tmp_path):
    u = Usage(input_tokens=2000, output_tokens=1500, cache_creation_input_tokens=1000, cache_read_input_tokens=4000)
    assert cost_usd("claude-opus-5-5", u) == pytest.approx((2000 * 4 + 1000 * 8 + 4000 * 0.2 + 1500 * 20) / 1e6)
    assert cost_usd("unknown-model", u) > cost_usd("claude-opus-5-5", u)          # never under-counts
    led = SpendLedger(tmp_path / "s.jsonl", cap_usd=0.05)
    led.record(model="claude-opus-5-5", usage=u, request_id="r", purpose="t")
    assert led.total() == pytest.approx(cost_usd("claude-opus-5-5", u))
    with pytest.raises(SpendCapReached):
        led.check(0.02)


# ---- manager loop with a fake Claude --------------------------------------------------
class FakeClaude:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def resp(stop, content, model="claude-opus-5-5"):
    return NS(stop_reason=stop, content=content, model=model, _request_id="req_x",
              usage=NS(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0))


def tool_use(i, name, args):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=args)


def text(s):
    return NS(type="text", text=s)


def test_manager_tool_loop_then_structured_playbook(tmp_path):
    b = make_bundle()
    final = out().model_dump_json()
    fake = FakeClaude([resp("tool_use", [tool_use(1, "market_brief", {"market": "USDTTMN"}),
                                         tool_use(2, "calendar", {})]),
                       resp("end_turn", [text(final)])])
    led = SpendLedger(tmp_path / "s.jsonl", cap_usd=5)
    r = run_manager(b, fake, led)
    assert r.out.regime == "devaluation_drift" and r.tool_calls == 2 and r.requests == 2
    assert r.cost_usd == pytest.approx(2 * (1000 * 4 + 500 * 20) / 1e6) == pytest.approx(led.total())
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["effort"] == "high" and kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "thinking" not in kw and "tool_choice" not in kw            # Opus 5.5: both would 400
    second = fake.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and second[-1]["content"][0]["is_error"] is False
    assert [t["role"] for t in r.transcript] == ["user", "assistant", "user", "assistant"]


def test_manager_tool_budget_and_unknown_tools(tmp_path):
    many = [tool_use(i, "calendar", {}) for i in range(7)] + [tool_use(9, "place_order", {})]
    fake = FakeClaude([resp("tool_use", many), resp("end_turn", [text(out().model_dump_json())])])
    run_manager(make_bundle(), fake, SpendLedger(tmp_path / "s.jsonl", 5))
    results = fake.calls[1]["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [False] * 6 + [True, True]


@pytest.mark.parametrize("r,msg", [
    (resp("refusal", []), "refused"),
    (resp("max_tokens", []), "max_tokens"),
    (resp("end_turn", [text("{not json")]), "not a valid playbook"),
])
def test_manager_failures_raise(tmp_path, r, msg):
    with pytest.raises(ManagerError, match=msg):
        run_manager(make_bundle(), FakeClaude([r]), SpendLedger(tmp_path / "s.jsonl", 5))


def test_spend_cap_blocks_before_any_request(tmp_path):
    fake = FakeClaude([])
    with pytest.raises(SpendCapReached):
        run_manager(make_bundle(), fake, SpendLedger(tmp_path / "s.jsonl", cap_usd=0.01))
    assert fake.calls == []


