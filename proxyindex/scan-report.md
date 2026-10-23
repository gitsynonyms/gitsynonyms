# 代理全量扫描报告 (proxy full scan)

- run: `20261023T122103Z-268f05`
- 开始: 2026-10-23T12:21:03Z
- 结束: 2026-10-23T12:24:21Z
- 探测目标: https://www.youtube.com/generate_204
- 部分完成: yes

## 覆盖 (coverage)

| 项目 | 数值 |
| --- | --- |
| 检索矩阵查询 | 51973 |
| 检索矩阵主题 | 32 |
| 矩阵游标覆盖 | 0.02% |
| 已发现来源 (list²) | 72813 |
| 候选代理 | 693502 |
| TCP 已探测 | 10500 (1.51%) |
| HTTP 已探测 | 9453 |
| HTTP 可用 | 119 |

未探测的候选按 `unknown` 计，不按 `ok` 计。

## Top 10 (按实测时延)

| # | proxy | 时延 (ms) | HTTP | 首次来源 |
| --- | --- | --- | --- | --- |
| 1 | `http://1.20.101.35:80` | 1303.3 | 204 | `https://raw.githubusercontent.com/6yyn/free-proxy-list/HEAD/http.txt` |
| 2 | `http://102.244.78.61:8080` | 1318.7 | 204 | `https://raw.githubusercontent.com/398001/ProxysList/main/all_working.txt` |
| 3 | `http://102.207.191.161:8081` | 1379.5 | 204 | `https://raw.githubusercontent.com/6yyn/free-proxy-list/HEAD/http.txt` |
| 4 | `http://103.125.15.58:8085` | 1704.8 | 204 | `https://raw.githubusercontent.com/6yyn/free-proxy-list/HEAD/http.txt` |
| 5 | `http://1.20.101.26:80` | 1859.6 | 204 | `https://raw.githubusercontent.com/Ch4120N/Ch4120N-Proxy-List/master/countries/TH/proxies.txt` |
| 6 | `http://102.164.252.150:8080` | 1882.7 | 204 | `https://raw.githubusercontent.com/AmmarrBN/collect-proxy/main/proxyactive.txt` |
| 7 | `http://103.130.61.60:8081` | 2110.4 | 204 | `https://raw.githubusercontent.com/6yyn/free-proxy-list/HEAD/http.txt` |
| 8 | `http://103.106.115.63:8080` | 2208.3 | 204 | `https://raw.githubusercontent.com/ALIILAPRO/Proxy/HEAD/http.txt` |
| 9 | `http://103.120.76.45:8080` | 2620.7 | 204 | `https://raw.githubusercontent.com/ErcinDedeoglu/proxies/HEAD/proxies/http.txt` |
| 10 | `http://1.20.101.20:80` | 2622.7 | 204 | `https://raw.githubusercontent.com/Ch4120N/Ch4120N-Proxy-List/HEAD/proxies/all.txt` |

## 阶段 (stages)

| 阶段 | 结果 |
| --- | --- |
| matrix | `{"digest": "8e2ed50cfadd475a31e68b0e78d5a988abccd1bba95d91f7c935dbfa035da266", "cursor": 11, "cycles": 0, "total": 52005, "searched": 2, "coverage": 0.000212, "queries": 51973, "topics": 32, "observed_at": "2026-10-23T12:21:14Z"}` |
| sources | `{"sources": 72813, "recorded": 20607, "expanded": 52206, "repos": 1582, "patterns": 33}` |
| collect | `{"sources": 72813, "pending": 13717, "fetched": 8137, "failed": 6184, "empty": 1810, "entries": 39938, "unique_candidates": 20876, "partial": true}` |
| probe | `{"tcp": {"pending": 2500, "checked": 2500, "open": 2500, "partial": false}, "http": {"alive_tcp": 10484, "pending": 2500, "checked": 1469, "ok": 0, "errors": 0, "partial": true}}` |
| rank | `{"pool": 693502, "probed_ok": 119, "candidates": 60, "verified": 26}` |
