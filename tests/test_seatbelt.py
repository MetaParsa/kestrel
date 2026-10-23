"""Seatbelt details found by the crash drill (reports/crash_drill.py)."""
from engine import exits, flatten
from engine.portfolio import Portfolio
from tests.test_batch_a import cross, decisions
from tests.test_loop import NOW, PX, give_playbook, make_engine

BTC = 22_000_000_000.0


def pf_with(qty_btc: float) -> Portfolio:
    pf = Portfolio(5_000_000.0, NOW)
    p = pf.pos("BTCTMN", "core")
    p.qty, p.cost_tmn = qty_btc, qty_btc * BTC * 1.03          # 3% under water: past the core stop
    return pf


def test_dust_left_after_a_sell_is_not_sold_again():
    dust = pf_with(2.85e-10)                                  # ≈ 6 TMN, below Wallex's 50k minimum
    assert exits.check_exits(dust, {"BTCTMN": BTC, "USDTTMN": PX["USDTTMN"]}, NOW) == []
    assert flatten.flatten_proposals(dust, {"BTCTMN": BTC, "USDTTMN": PX["USDTTMN"]}, "market", NOW) == []
    assert exits.hard_flatten(dust, {"BTCTMN": BTC, "USDTTMN": PX["USDTTMN"]}, NOW) == []


def test_real_positions_still_sell():
    real = pf_with(9.02e-06)                                  # ≈ 200k TMN
    assert [p.kind for p in exits.check_exits(real, {"BTCTMN": BTC}, NOW)] == ["stop"]
    assert [p.kind for p in flatten.flatten_proposals(real, {"BTCTMN": BTC}, "market", NOW)] == ["flatten"]
    assert len(exits.hard_flatten(real, {"BTCTMN": BTC}, NOW)) == 1


def test_a_single_glitch_print_raises_no_daily_loss_alarm(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    for m in PX:
        market.set(m, PX[m] * 0.70)                           # one absurd sample
    clock.t += 10
    eng.tick()
    assert not any(e["event"] == "daily_loss_stop" for e in eng.logs.tail("events", 200))
    assert not [d for d in decisions(eng) if d["side"] == "SELL"]
