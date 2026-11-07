#!/usr/bin/env python3
"""按出入金流水回算历史快照的「净入金总额 / 真实总盈亏」。

口径：
    某日净入金 = base_usdt + 截至该日（含）的全部已记录出入金
    真实总盈亏 = 该日总资产 − 该日净入金

用途：早期快照写入时还没有 net_deposit 机制，字段缺失；或补录/修正过
data/manual_flow.json 后，需要把旧快照一起对齐。
每次运行都会重算全部快照，幂等可重复执行。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "history.json"
NET = ROOT / "data" / "net_deposit.json"
FLOW = ROOT / "data" / "manual_flow.json"


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    hist = load(HIST, [])
    base = float(load(NET, {}).get("base_usdt", 0) or 0)

    changed = 0
    cum = 0.0
    for snap in hist:
        # 累计到该日（含）的净出入金：直接用快照里的 netFlowUsdt，
        # 它已包含接口充提与手动划转，避免只算手动部分导致漏账
        cum += float(snap.get("netFlowUsdt", 0) or 0)
        net_dep = base + cum
        real = (snap.get("totalUsdt", 0) or 0) - net_dep
        pct = (real / net_dep * 100) if net_dep else 0.0

        new_vals = {
            "netDepositUsdt": round(net_dep, 6),
            "realPnlUsdt": round(real, 6),
            "realPnlPct": round(pct, 4),
        }
        for k, v in new_vals.items():
            if snap.get(k) != v:
                changed += 1
                break
        snap.update(new_vals)

    HIST.write_text(json.dumps(hist, ensure_ascii=False, indent=1) + "\n")
    print(f"回算完成：{len(hist)} 条快照，{changed} 条被修正；base={base:.2f}")


if __name__ == "__main__":
    main()
