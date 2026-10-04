# Bitget 资产与每日盈亏仪表盘

把 Bitget 上「放进理财(Earn)的币无法计入盈利」的盲区补上：**同时读取现货 + 理财(Earn) 持仓**，折算 USDT，每天自动记录净值快照，并在 GitHub Pages 网页上展示总资产、现货/理财构成、每日盈亏与币种分布。

- 抓数脚本零第三方依赖（仅 Python 标准库）
- 每日北京时间 08:10 自动运行（GitHub Actions）
- 密钥全部走仓库 Secrets，不进入代码
- 网页为单文件自包含页面，读取 `data/history.json` 动态渲染（ECharts）

## 目录结构

```
bitget-pnl-dashboard/
├── fetch_pnl.py                 # 抓数脚本（现货 + 理财 + 行情 → 快照 → 写 history.json）
├── data/history.json            # 每日快照累计（初始为空数组）
├── index.html                   # GitHub Pages 仪表盘（单文件）
├── .github/workflows/daily.yml  # 每日定时抓数 + 提交历史 + 部署 Pages
└── README.md
```

## 一、部署到 GitHub（5 步）

1. **建仓库并推送**：在 GitHub 新建一个仓库（Public 才能用免费 Pages），把本目录所有文件推上去：
   ```bash
   git init && git add . && git commit -m "init" && git remote add origin <你的仓库地址> && git push -u origin main
   ```
2. **配置密钥（仓库 → Settings → Secrets and variables → Actions → New repository secret）**，添加三条，值为你在 Bitget 创建的 API 凭据：
   - `BITGET_API_KEY`
   - `BITGET_API_SECRET`
   - `BITGET_API_PASSPHRASE`

3. **开启 GitHub Pages**：仓库 → Settings → Pages → Source 选 **GitHub Actions**（不是 branch），保存。

4. **手动跑一次验证**：仓库 → Actions → 左侧选中「每日资产统计与 Pages 部署」→ Run workflow。跑完后：
   - 数据已写入 `data/history.json` 并提交
   - 页面地址为 `https://<你的用户名>.github.io/<仓库名>/`

5. **等待自动运行**：此后每天 08:10（北京时间）自动抓取并刷新页面。

## 二、安全提示（重要）

你此前把 API Key 贴在了对话里，**该 Key 等于已泄露**。部署成功、确认能正常抓数后，请到 Bitget 后台**删除旧 Key 并重新生成一个**，只勾选「只读 / Read-Only」权限（不要开通交易、提币），再把新 Key 更新到上述三条 Secrets。

## 三、数据口径

| 指标 | 含义 |
| --- | --- |
| 总资产 | 现货 + 理财(Earn) 各币持仓 × USDT 价格之和 |
| 今日净值变化 | 今日总净值 − 上一快照总净值（**含当日充提与币价波动**） |
| 累计变化 | 相对首日快照的净值增量 |
| 行情盈亏 | 仅按各币当日涨跌幅 × 持仓市值估算，不含充提（市场驱动参考） |
| 币种分布 | 最新快照各币 USDT 市值，现货+理财合并，取前 8 名 |

- 快照按北京时间计日；同一天重复运行会**覆盖当日快照**，不会产生重复记录。
- 理财接口优先 `/api/v2/earn/savings/assets`（理财宝持仓明细），不可用时自动降级到 `/api/v2/earn/account/assets`。
- 无 USDT 行情的币不纳入总值，会在脚本日志中提示。

## 四、本地调试

```bash
# 无需密钥与网络，验证解析与盈亏逻辑
python3 fetch_pnl.py --mock

# 本地真实抓取（先注入环境变量）
export BITGET_API_KEY=xxx BITGET_API_SECRET=xxx BITGET_API_PASSPHRASE=xxx
python3 fetch_pnl.py

# 本地预览页面
python3 -m http.server 8080   # 打开 http://localhost:8080（页面需通过 http 才能读取 history.json）
```

## 常见问题

- **页面一直「暂无数据」**：确认工作流已运行成功、`data/history.json` 已非空、Pages 部署用的是 GitHub Actions 源。
- **抓数失败**：看 Actions 运行日志；多为密钥错误、权限不足或理财接口未开放，脚本会打印具体接口与原因。
- **想改运行时间**：编辑 `.github/workflows/daily.yml` 里的 `cron`（UTC 时间）。

## PoolX 等锁仓产品（无公开 API）怎么统计

经过两轮 60+ 端点探测、SDK 与官方文档核查，**Bitget 公开 API 不提供 PoolX 持仓接口**（连账户总览接口都不含）。因此锁仓期间这些币无法自动读取，采用「手动补充 + 结束自动失效」机制。

### 推荐方式：Actions 表单更新（无需改 JSON）

1. 打开仓库 **Actions** 页面，选中左侧「每日资产统计与 Pages 部署」工作流。
2. 点右侧 **Run workflow**，展开表单后填写：
   - `poolx_coin`：**单币种**填代码（如 `BTC`），配合下方数量/日期；**多币种一次填** `币种:数量:结束日期,币种:数量:结束日期`（如 `BTC:0.0717:2026-10-30,SOL:43.6876:2026-10-30`）；删除多币种填 `BTC,SOL`
   - `poolx_amount`：单币种模式的锁仓数量（多币种模式忽略此框）
   - `poolx_ends`：单币种模式的结束日期（多币种模式忽略此框）
   - `poolx_action`：`update`（新增/覆盖）或 `remove`（删除）
3. 点绿色 **Run workflow**，约 30 秒后自动完成：更新配置 → 重新抓数 → 刷新仪表盘 → Bark 推送。
4. 只重新抓数不动 PoolX 时，所有 PoolX 字段留空即可。

> 手机端建议用 Safari/Chrome 打开 github.com（GitHub App 可能不显示表单输入框）。

### 备用方式：直接编辑 JSON

编辑 `data/poolx_manual.json`：
```json
{
  "BTC": { "amount": 0.0717, "ends": "2026-10-04" },
  "SOL": { "amount": 43.6876, "ends": "2026-10-04" }
}
```
- 抓数时脚本会把这些数量并入理财持仓统计，并在日志中标注「手动补充锁仓持仓」。
- 到 `ends` 日期后该条目**自动失效**——届时币已回到现货账户，由现货接口自动统计，不会重复计数。
