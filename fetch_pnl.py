#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitget 资产与每日盈亏抓取脚本（现货 + 理财/Earn）

作用：把「放进理财(Earn)的币无法计入盈利」的问题补上 —— 同时读取现货账户
与理财宝(Earn Savings)持仓，折算成 USDT，计算每日净值变化，并追加到
data/history.json，供 GitHub Pages 仪表盘展示。

读取环境变量（建议通过 GitHub Secrets 注入，勿硬编码进代码）：
  BITGET_API_KEY        - API Key（务必在交易所后台设为「只读」权限）
  BITGET_API_SECRET     - Secret
  BITGET_API_PASSPHRASE - Passphrase

运行：
  python3 fetch_pnl.py
本地无密钥测试（不联网、不写入，仅验证解析与盈亏逻辑）：
  python3 fetch_pnl.py --mock

依赖：仅 Python 标准库（urllib / hashlib / hmac）。
"""
import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://api.bitget.com"
ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "history.json"
BEIJING = timezone(timedelta(hours=8))  # 快照按北京时间计日，与 cron(UTC 00:10≈北京08:10) 对齐

OK_CODE = "00000"


# ---------------------------------------------------------------- 鉴权 ----------
def sign(secret: str, timestamp: str, method: str, request_path: str, body: str) -> str:
    """Bitget v2 签名：BASE64( HMAC_SHA256( secret, timestamp + method + requestPath + body ) )"""
    message = f"{timestamp}{method}{request_path}{body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def api_get(path: str, api_key: str, secret: str, passphrase: str) -> list | dict:
    """带鉴权的 GET 请求，返回响应中的 data 字段。失败时抛出带响应体的异常。"""
    timestamp = str(int(time.time() * 1000))
    req = Request(BASE + path, method="GET")
    req.add_header("ACCESS-KEY", api_key)
    req.add_header("ACCESS-SIGN", sign(secret, timestamp, "GET", path, ""))
    req.add_header("ACCESS-TIMESTAMP", timestamp)
    req.add_header("ACCESS-PASSPHRASE", passphrase)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {path}: {detail}")
    except URLError as e:
        raise RuntimeError(f"网络错误 {path}: {e.reason}")
    if payload.get("code") != OK_CODE:
        raise RuntimeError(f"接口报错 {path}: code={payload.get('code')} msg={payload.get('msg')}")
    return payload.get("data")


# ---------------------------------------------------------------- 数据源 ----------
def _num(x) -> float:
    """字符串/数字转 float，空或异常一律按 0，避免解析失败中断整条流水线。"""
    if x is None or x == "":
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def fetch_spot(api_key: str, secret: str, passphrase: str) -> dict:
    """现货账户资产 -> { coin: 数量(含挂单冻结) }"""
    data = api_get("/api/v2/spot/account/assets", api_key, secret, passphrase)
    out = {}
    for it in data or []:
        coin = (it.get("coin") or "").upper()
        if not coin:
            continue
        # available + availableInOrder + frozen + locked：任一存在字段都计入，缺省按 0
        total = sum(_num(it.get(k)) for k in ("available", "availableInOrder", "frozen", "locked"))
        if total > 1e-12:
            out[coin] = total
    return out


def fetch_earn(api_key: str, secret: str, passphrase: str) -> dict:
    """
    理财(Earn)持仓 -> { coin: 数量 }
    优先用「理财宝持仓明细」/api/v2/earn/savings/assets（totalAmount=本金+利息）；
    若不可用则退回「理财账户资产」/api/v2/earn/account/assets。
    """
    data = None
    try:
        data = api_get("/api/v2/earn/savings/assets", api_key, secret, passphrase)
    except Exception as exc:  # 不同账号开放的产品接口不同，失败则降级
        print(f"[warn] /api/v2/earn/savings/assets 不可用：{exc}")
    if not isinstance(data, list):
        data = None
    if data is None:
        try:
            data = api_get("/api/v2/earn/account/assets", api_key, secret, passphrase)
            print("[info] 改用 /api/v2/earn/account/assets 读取理财资产")
        except Exception as exc:
            raise RuntimeError(f"理财资产接口均不可用：{exc}")
    out = {}
    for it in data or []:
        coin = (it.get("coin") or "").upper()
        if not coin:
            continue
        total = _num(it.get("totalAmount")) or _num(it.get("amount")) or _num(it.get("balance"))
        if total > 1e-12:
            out[coin] = total
    return out


def fetch_prices() -> dict:
    """公开行情 -> { COIN: {"price": 浮点价, "change24h": 百分数(0~100) 或 None} }"""
    data = api_get("/api/v2/spot/market/tickers", "", "", "")
    out = {}
    for t in data or []:
        symbol = t.get("symbol") or ""
        if not symbol.endswith("USDT"):
            continue
        coin = symbol[:-4].upper()
        price = _num(t.get("lastPr")) or _num(t.get("close"))
        if price <= 0:
            continue
        # 24h 涨跌幅：优先读官方字段，读不到就用 openUtc0 推算
        chg = None
        for k in ("changeUtc24h", "change24h"):
            v = t.get(k)
            if v not in (None, "", "0"):
                chg = _num(v)
                break
        if chg is None:
            open0 = _num(t.get("openUtc0"))
            if open0 > 0:
                chg = (price - open0) / open0 * 100.0
        out[coin] = {"price": price, "change24h": chg}
    # 稳定币/USD 计价币固定为 1
    for stable in ("USDT", "USDC", "TUSD", "USD"):
        out[stable] = {"price": 1.0, "change24h": 0.0}
    return out


# ---------------------------------------------------------------- 汇总与盈亏 ----------
def build_snapshot(spot: dict, earn: dict, prices: dict) -> dict:
    by_coin = []
    total_usdt = 0.0
    spot_usdt = 0.0
    earn_usdt = 0.0
    market_pnl = 0.0
    missing = []

    for kind, holdings in (("spot", spot), ("earn", earn)):
        for coin, amount in holdings.items():
            price_info = prices.get(coin)
            if price_info is None or price_info["price"] <= 0:
                missing.append(coin)
                continue
            price = price_info["price"]
            usdt = amount * price
            chg = price_info["change24h"]
            if chg is not None:
                market_pnl += usdt * chg / 100.0
            total_usdt += usdt
            if kind == "spot":
                spot_usdt += usdt
            else:
                earn_usdt += usdt
            by_coin.append({"coin": coin, "amount": amount, "price": price,
                            "usdt": round(usdt, 6), "kind": kind})

    return {
        "date": datetime.now(BEIJING).strftime("%Y-%m-%d"),
        "ts": int(time.time() * 1000),
        "totalUsdt": round(total_usdt, 6),
        "spotUsdt": round(spot_usdt, 6),
        "earnUsdt": round(earn_usdt, 6),
        "marketPnlUsdt": round(market_pnl, 6),      # 仅按行情 24h 变动估算，不含充提
        "coinCount": len({e["coin"] for e in by_coin}),
        "byCoin": sorted(by_coin, key=lambda x: -x["usdt"]),
        "missing": missing,
    }


def load_history() -> list:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text("utf-8"))
        except Exception:
            return []
    return []


def save_history(history: list):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), "utf-8")


def main():
    parser = argparse.ArgumentParser(description="Bitget 资产与每日盈亏抓取")
    parser.add_argument("--mock", action="store_true", help="使用本地样例数据验证解析与盈亏逻辑（不联网不落盘）")
    args = parser.parse_args()

    if args.mock:
        run_mock()
        return

    missing = [k for k in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE")
               if not __import__("os").environ.get(k)]
    if missing:
        sys.exit(f"缺少环境变量：{', '.join(missing)}。请通过 GitHub Secrets 或 export 注入。")

    api_key = __import__("os").environ["BITGET_API_KEY"]
    secret = __import__("os").environ["BITGET_API_SECRET"]
    passphrase = __import__("os").environ["BITGET_API_PASSPHRASE"]

    print("[1/4] 读取现货账户资产…")
    spot = fetch_spot(api_key, secret, passphrase)
    print(f"      现货币种数：{len(spot)}")
    print("[2/4] 读取理财(Earn)持仓…")
    earn = fetch_earn(api_key, secret, passphrase)
    print(f"      理财币种数：{len(earn)}")
    print("[3/4] 拉取行情价…")
    prices = fetch_prices()
    print(f"      行情币种数：{len(prices)}")
    snap = build_snapshot(spot, earn, prices)
    if snap["missing"]:
        print(f"[warn] 以下币种无 USDT 行情，未计入总值：{', '.join(snap['missing'])}")

    history = load_history()
    replaced = False
    for i, h in enumerate(history):
        if h.get("date") == snap["date"]:
            history[i] = snap  # 同日重跑则覆盖，避免重复快照
            replaced = True
            break
    if not replaced:
        history.append(snap)

    if len(history) >= 2:
        prev, cur = history[-2], history[-1]
        daily = cur["totalUsdt"] - prev["totalUsdt"]
        cur["dailyChangeUsdt"] = round(daily, 6)
        cur["dailyChangePct"] = round(daily / prev["totalUsdt"] * 100, 6) if prev["totalUsdt"] else 0.0
    base = history[0]
    cum = snap["totalUsdt"] - base["totalUsdt"]
    snap["cumChangeUsdt"] = round(cum, 6)
    snap["cumChangePct"] = round(cum / base["totalUsdt"] * 100, 6) if base["totalUsdt"] else 0.0
    if replaced:
        history[-1].update(snap)

    save_history(history)
    print("[4/4] 已写入 data/history.json")
    print(f"      日期        : {snap['date']}")
    print(f"      总资产(USDT): {snap['totalUsdt']:,.4f}")
    print(f"      现货        : {snap['spotUsdt']:,.4f}")
    print(f"      理财        : {snap['earnUsdt']:,.4f}")
    print(f"      日净值变化  : {snap.get('dailyChangeUsdt', 0):+,.4f}")
    print(f"      累计变化    : {snap['cumChangeUsdt']:+,.4f}（自 {base['date']}）")


# ---------------------------------------------------------------- mock 自检 ----------
MOCK_PRICES = {
    "BTC": {"price": 61000, "change24h": 2.5},
    "ETH": {"price": 3000, "change24h": -1.2},
    "USDT": {"price": 1.0, "change24h": 0.0},
}


def run_mock():
    """用样例数据跑一遍解析与盈亏计算，校验字段名、缺字段容错与日期逻辑。"""
    spot = {"BTC": 0.5, "USDT": 300.0, "SOME_DEAD_COIN": 1.0}
    earn = {"BTC": 0.2, "ETH": 5.0}
    prices = dict(MOCK_PRICES)
    snap = build_snapshot(spot, earn, prices)
    assert snap["date"] and snap["ts"] > 0, "日期/时间戳缺失"
    assert abs(snap["totalUsdt"] - (0.7 * 61000 + 5 * 3000 + 300)) < 0.01, "总资产计算错误"
    assert abs(snap["spotUsdt"] - (0.5 * 61000 + 300)) < 0.01, "现货计算错误"
    assert abs(snap["earnUsdt"] - (0.2 * 61000 + 5 * 3000)) < 0.01, "理财计算错误"
    assert "SOME_DEAD_COIN" in snap["missing"], "无行情币种应进入 missing"
    assert snap["coinCount"] == 3, "coinCount 应为有行情的币种数"
    # 行情驱动盈亏：BTC 0.7*61000*2.5% - ETH 5*3000*1.2% + USDT 0
    expect = 0.7 * 61000 * 0.025 - 5 * 3000 * 0.012
    assert abs(snap["marketPnlUsdt"] - expect) < 0.01, "行情盈亏计算错误"
    print("mock 自检通过：字段解析、缺字段容错、现货/理财拆分、行情盈亏 均正确 ✅")
    print(json.dumps(snap, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
