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
    # 理财宝系列
    "/api/v2/earn/savings/account",
    "/api/v2/earn/savings/assets",
    "/api/v2/earn/savings/assets?periodType=1",
    "/api/v2/earn/savings/assets?periodType=2",
    "/api/v2/earn/account/assets",
    # PoolX 各种命名
    "/api/v2/earn/poolx/account-assets",
    "/api/v2/earn/poolx/assets",
    "/api/v2/earn/poolx/position",
    "/api/v2/earn/poolx/positions",
    "/api/v2/earn/pool-x/account-assets",
    "/api/v2/earn/pool-x/assets",
    "/api/v2/earn/pool-x/positions",
    "/api/v2/earn/launchpool/account-assets",
    "/api/v2/earn/launchpool/assets",
    "/api/v2/earn/launchpool/positions",
    "/api/v2/earn/launchx/account-assets",
    "/api/v2/earn/launchx/assets",
    "/api/v2/earn/launchx/positions",
    # 其他理财类别
    "/api/v2/earn/shark-fin/account-assets",
    "/api/v2/earn/staking/account-assets",
    "/api/v2/earn/elite/account-assets",
    "/api/v2/earn/elite-assets",
    "/api/v2/earn/project/account-assets",
    "/api/v2/earn/project/assets",
    "/api/v2/earn/lock/account-assets",
    "/api/v2/earn/lock/assets",
    "/api/v2/earn/structured/account-assets",
    "/api/v2/earn/range-sniper/account-assets",
    # v3 系列
    "/api/v3/earn/account/assets",
    "/api/v3/earn/elite-assets",
    "/api/v3/earn/elite-product",
    "/api/v3/earn/poolx/account-assets",
    "/api/v3/earn/savings/assets",
    # 账户总览类
    "/api/v2/account/all-account-balance",
    "/api/v2/account/funding-assets",
    "/api/v2/account/assets",
    "/api/v2/account/spot-assets",
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
