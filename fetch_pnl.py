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
    """带鉴权的 GET 请求，返回响应中的 data 字段。失败时抛出带响应体的异常。网络错误自动重试 3 次。"""
    last_exc = None
    for attempt in range(3):
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
            if payload.get("code") != OK_CODE:
                raise RuntimeError(f"接口报错 {path}: code={payload.get('code')} msg={payload.get('msg')}")
            return payload.get("data")
        except HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise RuntimeError(f"HTTP {e.code} {path}: {detail}")
        except URLError as e:
            last_exc = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"网络错误 {path}: {e.reason}")
    raise RuntimeError(f"网络错误 {path}: {last_exc}")  # 理论上不可达


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


EARN_CATEGORIES = [
    # 名称, 接口路径。理财账户总览优先（若返回明细则覆盖面最全，含 PoolX 等全产品）。
    ("理财账户总览", "/api/v2/earn/account/assets"),
    ("理财宝活期",   "/api/v2/earn/savings/assets"),
    ("PoolX",        "/api/v2/earn/pool-x/account-assets"),
    ("PoolX-alias",  "/api/v2/earn/poolx/account-assets"),
    ("鲨鱼鳍",       "/api/v2/earn/shark-fin/account-assets"),
    ("质押",         "/api/v2/earn/staking/account-assets"),
]

AMOUNT_FIELDS = ("totalAmount", "amount", "balance", "lockAmount", "locked",
                 "available", "principal", "principalAmount", "positionAmount")


def _parse_holdings(items) -> dict:
    """把任意理财端点的返回列表解析为 { coin: 数量 }，字段名做防御性匹配。"""
    out = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        coin = (it.get("coin") or it.get("currency") or it.get("baseCoin") or "").upper()
        if not coin:
            continue
        amt = 0.0
        for f in AMOUNT_FIELDS:
            amt = _num(it.get(f))
            if amt > 1e-12:
                break
        if amt > 1e-12:
            out[coin] = amt
    return out


def fetch_earn(api_key: str, secret: str, passphrase: str) -> dict:
    """
    理财(Earn)持仓 -> { coin: 数量 }
    运行时探测多个理财端点（理财宝 / PoolX / 鲨鱼鳍 / 质押 / 账户总览），
    逐个打印覆盖日志：
      - 若「理财账户总览」返回了币种明细，直接采用它（覆盖全部产品，避免重复计）；
      - 否则把各分类端点结果合并（同币种取较大值，防止重复统计）。
    """
    results = {}
    for label, path in EARN_CATEGORIES:
        try:
            data = api_get(path, api_key, secret, passphrase)
            h = _parse_holdings(data if isinstance(data, list) else [data])
            results[label] = h
            detail = ", ".join(f"{c} {v:.6g}" for c, v in h.items()) or "（空）"
            print(f"      [{label}] {path}：{len(h)} 币种 → {detail}")
        except Exception as exc:
            results[label] = {}
            print(f"      [{label}] {path}：不可用（{str(exc)[:60]}）")

    primary = results.get("理财账户总览")
    if primary and sum(primary.values()) > 1e-9:
        print("      [info] 采用「理财账户总览」作为理财持仓（含 PoolX 等全产品）")
        return primary

    merged = {}
    for label, h in results.items():
        if label == "理财账户总览":
            continue
        for coin, amt in h.items():
            merged[coin] = max(merged.get(coin, 0.0), amt)
    print(f"      [info] 采用分类端点合并结果作为理财持仓（{len(merged)} 币种）")
    return merged


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
def build_snapshot(spot: dict, earn: dict, prices: dict,
                   deposit_usdt: float = 0.0, withdraw_usdt: float = 0.0,
                   flow_note: str = "") -> dict:
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
        "depositUsdt": round(deposit_usdt, 6),       # 今日充入（USDT 等值）
        "withdrawUsdt": round(withdraw_usdt, 6),      # 今日提出（USDT 等值）
        "netFlowUsdt": round(deposit_usdt - withdraw_usdt, 6),  # 今日净出入金
        "flowNote": flow_note,                          # 接口不可用时的说明
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


POOLX_FILE = ROOT / "data" / "poolx_manual.json"


def load_manual_poolx() -> dict:
    """
    手动补充无法通过 API 读取的锁仓持仓（如 PoolX）。
    格式：{"BTC": {"amount": 0.0717, "ends": "2026-10-04"}, ...}
    结束日期(ends)已过的条目自动忽略——此时币已回到现货账户，由 API 自动统计，避免重复计。
    """
    if not POOLX_FILE.exists():
        return {}
    try:
        raw = json.loads(POOLX_FILE.read_text("utf-8"))
    except Exception as exc:
        print(f"[warn] 读取 {POOLX_FILE.name} 失败：{exc}")
        return {}
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    out = {}
    for coin, cfg in (raw or {}).items():
        if not isinstance(cfg, dict):
            continue
        ends = str(cfg.get("ends") or "")
        if ends and ends < today:
            print(f"      [info] {coin} PoolX 已于 {ends} 结束，跳过手动补充（届时由现货接口自动统计）")
            continue
        amt = _num(cfg.get("amount"))
        if amt > 1e-12:
            out[coin.upper()] = amt
    return out


def fetch_wallet_records(api_key, secret, passphrase, path: str):
    """
    读取今日(北京时间)充币/提币记录。
    path 如 /api/v2/spot/wallet/deposit-records 或 /withdrawal-records。
    返回 list（记录列表）；None 表示接口不可用/未授权（需与"今日无记录=[]"区分）。
    """
    now = datetime.now(BEIJING)
    start_ms = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    qs = f"?startTime={start_ms}&endTime={end_ms}&pageSize=100"
    try:
        data = api_get(api_key, secret, passphrase, path + qs)
    except Exception as exc:
        print(f"[warn] 读取 {path} 失败：{exc}")
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "resultList", "records", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def sum_flow_usdt(records: list, prices: dict) -> float:
    """把充提记录按币种换算成 USDT 求和，只统计成功/已完成的记录。"""
    if not records:
        return 0.0
    total = 0.0
    skip_status = {"pending", "processing", "failed", "canceled", "cancelled",
                    "reviewing", "wait_review", "init", "waiting"}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        status = str(rec.get("status") or rec.get("state") or "").lower()
        if status in skip_status:
            continue
        coin = str(rec.get("coin") or rec.get("coinName") or rec.get("coinSymbol") or "").upper()
        amount = _num(rec.get("amount") or rec.get("quantity") or rec.get("amountStr"))
        if not coin or amount <= 0:
            continue
        price_info = prices.get(coin)
        price = price_info["price"] if price_info and price_info["price"] > 0 else 1.0
        total += amount * price
    return total


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

    print("[1/5] 读取现货账户资产…")
    spot = fetch_spot(api_key, secret, passphrase)
    print(f"      现货币种数：{len(spot)}")
    print("[2/5] 读取理财(Earn)持仓…")
    earn = fetch_earn(api_key, secret, passphrase)
    print(f"      理财币种数：{len(earn)}")
    # 手动补充 PoolX 等无 API 的锁仓持仓（带结束日期，自动失效）
    manual = load_manual_poolx()
    if manual:
        print(f"      [info] 手动补充锁仓持仓：{', '.join(f'{c} {v}' for c, v in manual.items())}")
        for coin, amt in manual.items():
            earn[coin] = earn.get(coin, 0.0) + amt
        print(f"      补充后理财币种数：{len(earn)}")
    print("[3/5] 拉取行情价…")
    prices = fetch_prices()
    print(f"      行情币种数：{len(prices)}")
    print("[4/5] 读取今日充提记录…")
    dep = fetch_wallet_records(api_key, secret, passphrase, "/api/v2/spot/wallet/deposit-records")
    wit = fetch_wallet_records(api_key, secret, passphrase, "/api/v2/spot/wallet/withdrawal-records")
    flow_note = ""
    if dep is None or wit is None:
        flow_note = "充提记录接口未授权或不可用，出入金暂显示为 0"
        dep_usdt = wit_usdt = 0.0
    else:
        dep_usdt = sum_flow_usdt(dep, prices)
        wit_usdt = sum_flow_usdt(wit, prices)
    print(f"      今日充入 {dep_usdt:.2f} / 提出 {wit_usdt:.2f} USDT"
          + (f"（{flow_note}）" if flow_note else ""))
    snap = build_snapshot(spot, earn, prices, dep_usdt, wit_usdt, flow_note)
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
