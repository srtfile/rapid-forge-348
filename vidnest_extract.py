#!/usr/bin/env python3
"""
VidNest stream extractor - all 10 servers, streams only.
"""

import argparse
import base64
import json
import re
import sys
import urllib.parse
import requests

CUSTOM_CIPHER_ALPHABET = "RB0fpH8ZEyVLkv7c2i6MAJ5u3IKFDxlS1NTsnGaqmXYdUrtzjwObCgQP94hoeW+/="

SERVER_PATHS = {
    "catflix": "movies4f",
    "ophim":   "klikxxi",
    "alfa":    "moviesapi",
    "beta":    "purstream",
    "lamda":   "allmovies",
    "prime":   "catflix",
    "hexa":    "vidlink",
    "sigma":   "hollymoviehd",
    "gama":    "flixhq",
    "delta":   "allmovies",
}

WORKER_BASE = "https://nameless-mountain-a9f1.vidnest-1.workers.dev"
WORKER_HEADERS = {
    "Origin":     "https://fmoviesunblocked.net",
    "Referer":    "https://fmoviesunblocked.net/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

PROXY_BASES = {
    "beta":  "https://tiktoks.animanga.fun/hls",
    "hexa":  "https://megacloud.animanga.fun/proxy",
    "sigma": "https://upcloud.animanga.fun/proxy",
}

PROXY_HEADERS = {
    "hexa":  {"Origin": "https://vidlink.pro",    "Referer": "https://vidlink.pro/",    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    "sigma": {"Origin": "https://flashstream.cc", "Referer": "https://flashstream.cc/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
}

FETCH_HEADERS = {
    "Referer":    "https://vidnest.fun/",
    "Origin":     "https://vidnest.fun",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, */*",
}

SUBTITLE_HOSTS = ("opensubtitles.org", "opensubtitles.com", "wyzie.io", "vdrk.site")
SUBTITLE_EXTS  = (".vtt", ".srt", ".ass", ".ssa")


def decode(data: str) -> dict:
    values = {c: i for i, c in enumerate(CUSTOM_CIPHER_ALPHABET)}
    out = bytearray()
    data = re.sub(r"\s+", "", data)
    for i in range(0, len(data), 4):
        chunk = (data[i:i+4] + "====")[:4]
        n = [values.get(c, 64) for c in chunk]
        out.append((n[0] << 2) | (n[1] >> 4))
        if n[2] != 64: out.append(((n[1] & 15) << 4) | (n[2] >> 2))
        if n[3] != 64: out.append(((n[2] & 3) << 6) | n[3])
    return json.loads(out.decode("utf-8", errors="replace"))


def detect_type(url: str) -> str:
    u = url.lower().split("?")[0]
    if u.endswith((".m3u8", ".txt")) or any(k in u for k in ("master", "playlist", "streamsvr", "/pl/", "/hls/")):
        return "hls"
    if u.endswith(".mpd"):  return "dash"
    if u.endswith(".mp4"):  return "mp4"
    if u.endswith(".webm"): return "webm"
    return "hls"


def is_subtitle(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in SUBTITLE_HOSTS) or u.split("?")[0].endswith(SUBTITLE_EXTS)


def is_bare(url: str) -> bool:
    return not re.sub(r"https?://[^/]+", "", url).rstrip("/")


def wrap_proxy(server: str, upstream: str) -> str:
    base = PROXY_BASES.get(server)
    if not base:
        return upstream
    if server == "beta":
        b64 = base64.b64encode(upstream.encode()).decode().rstrip("=")
        return f"{base}/{b64}/master.m3u8"
    hdrs = PROXY_HEADERS.get(server, {})
    return base + "?" + urllib.parse.urlencode({
        "url": upstream,
        "headers": json.dumps(hdrs, separators=(",", ":")),
    })


def wrap_worker(upstream: str) -> str:
    return WORKER_BASE + "/mp4-proxy?" + urllib.parse.urlencode({
        "url": upstream,
        "headers": json.dumps(WORKER_HEADERS, separators=(",", ":")),
    })


def get_url(item: dict) -> str:
    return item.get("url") or item.get("file") or ""


def walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)
    else:
        yield obj


def extract_streams(payload: dict, server: str) -> list:
    inner = payload.get("data", payload) if isinstance(payload, dict) else payload
    raw_urls = []

    for scope in ([inner, payload] if inner is not payload else [payload]):
        if not isinstance(scope, dict):
            continue
        for src in scope.get("sources") or []:
            if not isinstance(src, dict): continue
            u = get_url(src)
            if u: raw_urls.append((u, src.get("quality") or src.get("type") or "auto"))
        dl = scope.get("downloads") or []
        if isinstance(dl, dict): dl = list(dl.values())
        for v in dl:
            if not isinstance(v, dict): continue
            u = get_url(v)
            if u: raw_urls.append((u, v.get("resolution") or v.get("quality") or "unknown"))
        for key in ("stream", "streamUrl", "stream_url", "hls", "url"):
            v = scope.get(key)
            if isinstance(v, str) and v.startswith("http"):
                raw_urls.append((v, "auto"))

    if not raw_urls:
        for node in walk(payload):
            if isinstance(node, str) and node.startswith("http"):
                raw_urls.append((node, "unknown"))

    seen, unique = set(), []
    for u, res in raw_urls:
        if u not in seen:
            seen.add(u)
            unique.append((u, res))

    out = []
    for raw_url, res in unique:
        if is_subtitle(raw_url) or is_bare(raw_url):
            continue
        if raw_url.lower().split("?")[0].endswith(".mp4"):
            proxy_url = wrap_worker(raw_url)
        elif server in PROXY_BASES:
            proxy_url = wrap_proxy(server, raw_url)
        else:
            proxy_url = raw_url
        out.append({"url": proxy_url, "raw_url": raw_url, "resolution": res})

    return out


def resolve(tmdb_id: int, media_type: str = "movie", season: str = "1", episode: str = "1"):
    print(f"\n{'='*60}", flush=True)
    print(f"  VidNest Extractor  |  ID: {tmdb_id}  |  {media_type}", flush=True)
    print(f"{'='*60}\n", flush=True)

    path = f"movie/{tmdb_id}" if media_type == "movie" else f"tv/{tmdb_id}/{season}/{episode}"
    all_streams = []
    server_log = []

    for server_name, backend in SERVER_PATHS.items():
        url = f"https://new.vidnest.fun/{backend}/{path}"
        print(f"[{server_name:8}] fetching...", flush=True)

        entry = {"server": server_name, "backend": backend, "status": None, "streams": []}

        try:
            r = requests.get(url, headers=FETCH_HEADERS, timeout=10)
            entry["status"] = r.status_code
        except Exception as e:
            print(f"           ✗ {e}\n", flush=True)
            entry["status"] = "error"
            entry["error"] = str(e)
            server_log.append(entry)
            continue

        if r.status_code != 200:
            print(f"           ✗ HTTP {r.status_code}\n", flush=True)
            server_log.append(entry)
            continue

        try:
            raw = r.json()
            enc_data = raw.get("data") if isinstance(raw, dict) else None
            if not enc_data:
                print(f"           ✗ no data field\n", flush=True)
                server_log.append(entry)
                continue

            try:
                payload = decode(enc_data)
            except Exception:
                payload = raw

            streams = extract_streams(payload, server_name)

            if streams:
                for s in streams:
                    mtype = detect_type(s["raw_url"])
                    print(f"           ✓ [{server_name:8}] [{mtype:4}] [{s['resolution']:>7}]", flush=True)
                    print(f"             {s['url']}", flush=True)
                    record = {
                        "server":     server_name,
                        "type":       mtype,
                        "resolution": s["resolution"],
                        "url":        s["url"],
                        "raw_url":    s["raw_url"],
                    }
                    all_streams.append(record)
                    entry["streams"].append(record)
                print(flush=True)
            else:
                print(f"           ✗ decoded but no stream URLs\n", flush=True)

        except Exception as e:
            print(f"           ✗ error: {e}\n", flush=True)
            entry["error"] = str(e)

        server_log.append(entry)

    print(f"\n{'='*60}", flush=True)
    print(f"  Done — {len(all_streams)} stream(s) found", flush=True)
    print(f"{'='*60}\n", flush=True)

    return {
        "tmdb_id":    tmdb_id,
        "media_type": media_type,
        "season":     season,
        "episode":    episode,
        "total":      len(all_streams),
        "streams":    all_streams,
        "servers":    server_log,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tmdb_id",    required=True)
    parser.add_argument("--media_type", default="movie")
    parser.add_argument("--season",     default="1")
    parser.add_argument("--episode",    default="1")
    parser.add_argument("--output",     default=None, help="Save JSON results to this file")
    args = parser.parse_args()

    result = resolve(
        tmdb_id    = int(args.tmdb_id),
        media_type = args.media_type,
        season     = args.season,
        episode    = args.episode,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {args.output}", flush=True)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))