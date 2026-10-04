#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时探测：充提记录端点的原始响应（禁用重定向），定位 DNS 错误原因。"""
import os, sys, time, json, urllib.request
from urllib.request import Request
from urllib.error import HTTPError, URLError

sys.path.insert(0, ".")
import fetch_pnl as f  # noqa: E402

K = os.environ.get("BITGET_API_KEY", "")
S = os.environ.get("BITGET_API_SECRET", "")
P = os.environ.get("BITGET_API_PASSPHRASE", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        print(f"  [redirect {code}] Location: {newurl}")
        for hk, hv in headers.items():
            if hk.lower() in ("location", "server", "content-type"):
                print(f"    {hk}: {hv}")
        return None  # 不跟随重定向


PATHS = [
    "/api/v2/spot/wallet/deposit-records?startTime=1791100800000&endTime=1791120000000&pageSize=100",
    "/api/v2/spot/wallet/deposit-records?startTime=1791100800000&endTime=1791120000000&pageSize=20",
    "/api/v2/spot/wallet/deposit-records?startTime=1791100800000&endTime=1791120000000&pageSize=50",
    "/api/v2/spot/wallet/withdrawal-records?startTime=1791100800000&endTime=1791120000000&pageSize=100",
]

opener = urllib.request.build_opener(NoRedirect)

for path in PATHS:
    print(f"\n>>> {path}")
    ts = str(int(time.time() * 1000))
    req = Request(f.BASE + path, method="GET")
    req.add_header("ACCESS-KEY", K)
    req.add_header("ACCESS-SIGN", f.sign(S, ts, "GET", path, ""))
    req.add_header("ACCESS-TIMESTAMP", ts)
    req.add_header("ACCESS-PASSPHRASE", P)
    req.add_header("Content-Type", "application/json")
    try:
        resp = opener.open(req, timeout=20)
        body = resp.read().decode("utf-8", "ignore")
        print(f"  HTTP {resp.status} | body: {body[:300]}")
    except HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        print(f"  HTTPError {e.code} | body: {body[:300]}")
    except URLError as e:
        print(f"  URLError: {e.reason}")
    except Exception as e:
        print(f"  Exception: {type(e).__name__}: {e}")

print("\n=== 探测结束 ===")
