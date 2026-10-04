#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
临时探测脚本：批量试探 Bitget 理财/账户相关端点是否存在及其返回内容。
仅用于定位 PoolX 等产品的正确接口，定位后即删除本脚本。
"""
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, ".")
import fetch_pnl as f  # noqa: E402

BASE = "https://api.bitget.com"
CANDIDATES = [
    # ===== 第二轮：v3 系列（40084=接口存在但需统一账户模式；404=不存在）=====
    "/api/v3/earn/account-assets",
    "/api/v3/earn/account/assets",
    "/api/v3/earn/poolx/account-assets",
    "/api/v3/earn/poolx/assets",
    "/api/v3/earn/poolx/positions",
    "/api/v3/earn/pool-x/account-assets",
    "/api/v3/earn/launchpool/account-assets",
    "/api/v3/earn/launchpool/assets",
    "/api/v3/earn/staking/account-assets",
    "/api/v3/earn/shark-fin/account-assets",
    "/api/v3/earn/savings/assets",
    "/api/v3/earn/savings/account",
    "/api/v3/earn/loan/account-assets",
    # ===== 第二轮：v2 更多候选 =====
    "/api/v2/earn/poolx-subscribe",
    "/api/v2/earn/poolx-order",
    "/api/v2/earn/poolx/order",
    "/api/v2/earn/mining/account-assets",
    "/api/v2/earn/farm/account-assets",
    "/api/v2/earn/stake/account-assets",
    "/api/v2/earn/project-order/account-assets",
    "/api/v2/earn/loan/account-assets",
    "/api/v2/earn/instloan/account-assets",
    "/api/v2/earn/overview",
    "/api/v2/earn/account",
    "/api/v2/earn/position",
    "/api/v2/earn/positions",
    "/api/v2/earn/holdings",
    "/api/v2/earn/assets",
    # 对照基线
    "/api/v2/earn/account/assets",
    "/api/v2/earn/savings/assets",
]

K = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BITGET_API_KEY", "")
S = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("BITGET_API_SECRET", "")
P = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("BITGET_API_PASSPHRASE", "")


def raw_get(path: str):
    """带鉴权的 GET，返回 (状态, 响应体前500字符)。"""
    ts = str(int(f.time.time() * 1000))
    req = Request(BASE + path, method="GET")
    req.add_header("ACCESS-KEY", K)
    req.add_header("ACCESS-SIGN", f.sign(S, ts, "GET", path, ""))
    req.add_header("ACCESS-TIMESTAMP", ts)
    req.add_header("ACCESS-PASSPHRASE", P)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "ignore")
            return "HTTP200", body[:500]
    except HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        return f"HTTP{e.code}", body[:400]
    except URLError as e:
        return "NETERR", str(e.reason)[:100]


print("=== Bitget 端点批量探测 ===")
for path in CANDIDATES:
    status, body = raw_get(path)
    print(f"\n>>> {path}")
    print(f"    {status} | {body}")
print("\n=== 探测结束 ===")
