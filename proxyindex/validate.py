#!/usr/bin/env python3
"""Check which pooled proxies actually work.

Stage 3 of the proxy index: a fast TCP reachability pass, then a real HTTP
CONNECT check through the survivors, so a proxy that merely accepts a socket is
not reported as usable.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).resolve().parent
POOL = OUT / "proxies.txt"
ALIVE_TCP = OUT / "alive_tcp.txt"
ALIVE_HTTP = OUT / "alive_http.txt"
CANARY = "http://example.com/"


async def probe(host: str, port: int, timeout: float, semaphore: asyncio.Semaphore) -> str | None:
    async with semaphore:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except Exception:  # noqa: BLE001 - unreachable proxies are expected
            return None
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return f"{host}:{port}"


async def tcp_pass(entries: list[str], concurrency: int, timeout: float) -> list[str]:
    semaphore = asyncio.Semaphore(concurrency)
    tasks = []
    for entry in entries:
        host, _, port = entry.rpartition(":")
        if not host or not port.isdigit():
            continue
        tasks.append(probe(host, int(port), timeout, semaphore))
    alive: list[str] = []
    for index, result in enumerate(asyncio.as_completed(tasks), 1):
        value = await result
        if value:
            alive.append(value)
        if index % 20000 == 0:
            print(f"  tcp {index}/{len(tasks)} checked, {len(alive)} open", flush=True)
    return alive


def http_check(proxy: str) -> str | None:
    try:
        result = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "--max-time", "8",
             "-x", f"http://{proxy}", "-w", "%{http_code}", CANARY],
            capture_output=True, text=True, timeout=12,
        )
    except subprocess.TimeoutExpired:
        return None
    return proxy if result.stdout.strip() == "200" else None


def main() -> int:
    entries = [line.strip() for line in POOL.open(encoding="utf-8") if line.strip()]
    print(f"pool: {len(entries)} unique proxies", flush=True)

    alive: list[str] = []
    chunk = 40_000
    for start in range(0, len(entries), chunk):
        alive.extend(asyncio.run(tcp_pass(entries[start:start + chunk], 600, 2.5)))
        print(f"  chunk {start}-{start + chunk}: open so far {len(alive)}", flush=True)
    ALIVE_TCP.write_text("\n".join(sorted(alive)) + "\n", encoding="utf-8")
    print(f"tcp open: {len(alive)} -> {ALIVE_TCP}", flush=True)

    working: list[str] = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        for index, result in enumerate(pool.map(http_check, alive), 1):
            if result:
                working.append(result)
            if index % 2000 == 0:
                print(f"  http {index}/{len(alive)} checked, {len(working)} working", flush=True)
    ALIVE_HTTP.write_text("\n".join(sorted(working)) + "\n", encoding="utf-8")
    print(f"http connect working: {len(working)} -> {ALIVE_HTTP}", flush=True)
    if alive:
        print(f"survival: tcp {len(alive) / len(entries):.2%}, http {len(working) / len(entries):.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
