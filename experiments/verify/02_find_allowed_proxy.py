"""
Allowed-Region Proxy Hunter — Polymarket geoblock bypass for TESTING ONLY
=========================================================================

The dev box (PL) is geoblocked by Polymarket. This pulls public free-proxy
lists, then tests each proxy against Polymarket's no-auth geoblock litmus
(`https://polymarket.com/api/geoblock` → {"blocked": bool, "country": ...}).
Prints proxies that land in an allowed region (blocked=false).

Use only to verify the sigtype-3 / allowlist behaviour with a THROWAWAY key.
Never route a funded wallet's traffic through a random public proxy.

USAGE:
    uv run --no-project --with httpx python experiments/verify/02_find_allowed_proxy.py
    uv run --no-project --with httpx python experiments/verify/02_find_allowed_proxy.py --limit 800 --workers 60
"""

import argparse
import concurrent.futures as cf
import functools

import httpx

GEO_LITMUS = "https://polymarket.com/api/geoblock"

SOURCES = [
    # geonode returns JSON; the rest are ip:port-per-line text
    "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&protocols=http%2Chttps",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]


def gather(limit: int) -> list[str]:
    """Pull candidate ip:port strings from public lists, de-duped."""
    seen: set[str] = set()
    with httpx.Client(timeout=20.0, follow_redirects=True) as c:
        for url in SOURCES:
            try:
                r = c.get(url)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                print(f"  source failed: {url.split('//')[1][:40]}… ({type(e).__name__})")
                continue
            if "geonode" in url:
                for row in r.json().get("data", []):
                    seen.add(f"{row['ip']}:{row['port']}")
            else:
                for line in r.text.splitlines():
                    line = line.strip()
                    if line.count(".") == 3 and ":" in line:
                        seen.add(line.split()[0])
    out = list(seen)[:limit]
    print(f"gathered {len(out)} unique candidates")
    return out


def test(proxy: str, timeout: float) -> tuple[str, str] | None:
    """Return (proxy, country) if it reaches the litmus and is NOT blocked."""
    url = f"http://{proxy}"
    try:
        with httpx.Client(proxy=url, timeout=timeout, follow_redirects=True) as c:
            r = c.get(GEO_LITMUS)
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("blocked") is False:
                return proxy, data.get("country", "?")
    except Exception:  # noqa: BLE001
        return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--workers", type=int, default=50)
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()

    candidates = gather(args.limit)
    if not candidates:
        print("no candidates gathered — public sources may be down")
        return

    print(f"testing {len(candidates)} proxies against {GEO_LITMUS} …")
    hits: list[tuple[str, str]] = []
    probe = functools.partial(test, timeout=args.timeout)
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(probe, candidates), 1):
            if res:
                hits.append(res)
                print(f"  ✅ ALLOWED: http://{res[0]}  (country={res[1]})")
            if i % 100 == 0:
                print(f"  …{i}/{len(candidates)} tested, {len(hits)} allowed so far")

    print(f"\n{len(hits)} allowed-region proxies found.")
    if hits:
        print("\nRun the probe through one, e.g.:")
        print(f"  HTTPS_PROXY=http://{hits[0][0]} HTTP_PROXY=http://{hits[0][0]} \\")
        print("    uv run python experiments/verify/01_eoa_allowlist_probe.py --fresh")


if __name__ == "__main__":
    main()
