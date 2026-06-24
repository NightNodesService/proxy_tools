from __future__ import annotations

import re
import time
from dataclasses import replace

import httpx

from .browser_checker import run_browser_target_check
from .models import AppSettings, ProxyCheckResult, TargetSite


BLOCK_STATUSES = {401, 403, 407, 418, 429, 451, 503}
CAPTCHA_HINTS = (
    "captcha",
    "robot check",
    "are you human",
    "verify you are human",
    "unusual traffic",
    "access denied",
)
DATACENTER_HINTS = (
    "amazon",
    "aws",
    "google",
    "microsoft",
    "azure",
    "digitalocean",
    "ovh",
    "hetzner",
    "linode",
    "vultr",
    "cloudflare",
)
GLOBAL_PING_NODES = {
    "n01": "Shanghai",
    "n02": "Hong Kong",
    "n03": "Tokyo",
    "n04": "Singapore",
    "n09": "Los Angeles",
    "n11": "Vancouver",
    "n13": "Frankfurt",
    "n15": "Paris",
}


def normalize_proxy(raw_proxy: str, proxy_type: str) -> str:
    host, port, username, password = parse_proxy_parts(raw_proxy)
    scheme = proxy_type.lower()
    return f"{scheme}://{username}:{password}@{host}:{port}"


def parse_proxy_parts(raw_proxy: str) -> tuple[str, str, str, str]:
    parts = raw_proxy.strip().split(":")
    if len(parts) != 4:
        raise ValueError("Proxy must use ip:port:user:pass format.")

    host, port, username, password = parts
    if not host or not port or not username or not password:
        raise ValueError("Proxy host, port, username, and password are required.")

    return host, port, username, password


def run_proxy_check(
    raw_proxy: str,
    proxy_type: str,
    target: TargetSite,
    mode: str,
    settings: AppSettings,
) -> ProxyCheckResult:
    result = ProxyCheckResult(
        proxy=raw_proxy.strip(),
        proxy_type=proxy_type,
        target_name=target.name,
        target_url=target.url,
        mode=mode,
    )

    try:
        proxy_url = normalize_proxy(raw_proxy, proxy_type)
        proxy_host, proxy_port, proxy_username, proxy_password = parse_proxy_parts(raw_proxy)
    except ValueError as exc:
        result.notes.append(str(exc))
        return score_result(result)

    headers = {"User-Agent": settings.user_agent}
    try:
        with httpx.Client(
            proxy=proxy_url,
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            ip_payload = fetch_ip_payload(client)
            result = apply_ip_payload(result, ip_payload)

            if mode == "Browser Simulation":
                browser_result = run_browser_target_check(
                    proxy_server=f"{proxy_type.lower()}://{proxy_host}:{proxy_port}",
                    proxy_username=proxy_username,
                    proxy_password=proxy_password,
                    target=target,
                    settings=settings,
                )
                result.reachable = result.reachable or browser_result.reachable
                result.target_reachable = browser_result.target_reachable
                result.latency_ms = browser_result.latency_ms
                result.blocked = browser_result.blocked
                result.captcha = browser_result.captcha
                result.notes.extend(browser_result.notes)
                result.tags.extend(browser_result.tags)
                return score_result(result)

            started = time.perf_counter()
            response = client.get(target.url)
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.target_reachable = response.status_code < 500
            result.reachable = True
            result.blocked = response.status_code in BLOCK_STATUSES

            body_sample = response.text[:12000].lower()
            result.captcha = any(hint in body_sample for hint in CAPTCHA_HINTS)

            result.notes.append(f"Target returned HTTP {response.status_code}.")
            if result.blocked:
                result.tags.append("blocked")
            if result.captcha:
                result.tags.append("captcha")
    except httpx.TimeoutException:
        result.notes.append("Request timed out.")
    except httpx.ProxyError as exc:
        result.notes.append(f"Proxy error: {exc}")
    except httpx.HTTPError as exc:
        result.notes.append(f"HTTP error: {exc}")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"Unexpected error: {exc}")

    return score_result(result)


def fetch_ip_payload(client: httpx.Client) -> dict[str, object]:
    try:
        trace_response = client.get("https://ip.net.coffee/cdn-cgi/trace")
        trace_response.raise_for_status()
        exit_ip = parse_cloudflare_trace_ip(trace_response.text)
        if not exit_ip:
            return {}

        lookup_response = client.get(f"https://ip.net.coffee/api/ip/lookup/{exit_ip}")
        lookup_response.raise_for_status()
        payload = lookup_response.json()
        if isinstance(payload, dict):
            payload["_source"] = "ip_intelligence_provider"
            payload["_global_latencies"] = fetch_global_latencies(client, exit_ip)
            return payload
    except httpx.HTTPError:
        return {}
    return {}


def parse_cloudflare_trace_ip(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("ip="):
            return line.partition("=")[2].strip()
    return ""


def apply_ip_payload(result: ProxyCheckResult, payload: dict[str, object]) -> ProxyCheckResult:
    if not payload:
        result.notes.append("IP intelligence provider did not return data.")
        return result

    exit_ip = str(payload.get("ip") or payload.get("query") or "Unknown")
    country, region = resolve_geo_location(payload)
    asn_number = payload.get("asn")
    as_name = str(payload.get("asname") or payload.get("asOrganization") or payload.get("company_name") or "")
    asn = f"AS{asn_number} {as_name}".strip() if asn_number else str(payload.get("org") or "Unknown")
    company_info = str(payload.get("company_name") or payload.get("asOrganization") or "Unknown")
    isp = str(payload.get("isp") or payload.get("company_name") or payload.get("asOrganization") or as_name or "Unknown")
    estimated_bandwidth = str(payload.get("asn_tbps") or "Unknown")

    ip_type = classify_ip_type_from_payload(payload, asn, isp)
    tags = [*result.tags]
    if ip_type == "Datacenter":
        tags.append("datacenter")
    elif ip_type == "Residential-like":
        tags.append("residential-like")

    for key, tag in (
        ("is_vpn", "vpn"),
        ("is_proxy", "proxy"),
        ("is_tor", "tor"),
        ("is_abuser", "abuser"),
        ("is_crawler", "crawler"),
    ):
        if payload.get(key):
            tags.append(tag)

    trust_score = payload.get("trust_score")
    cleanliness_score = result.cleanliness_score
    if isinstance(trust_score, int | float):
        cleanliness_score = max(0, min(100, round(float(trust_score))))

    ai_verdict = payload.get("ai_verdict")
    if isinstance(ai_verdict, dict):
        label = ai_verdict.get("label")
        confidence = ai_verdict.get("confidence")
        if label:
            result.notes.append(f"IP intelligence verdict: {label} ({confidence or '-'} confidence).")
    if payload.get("_source"):
        result.notes.append("IP profile source: integrated intelligence provider.")

    return replace(
        result,
        reachable=True,
        exit_ip=exit_ip,
        country=country,
        region=region,
        asn=asn,
        isp=isp,
        ip_type=ip_type,
        company_info=company_info,
        operator_type=normalize_operator_type(payload),
        human_traffic=detect_human_traffic(payload),
        ip_native=detect_ip_native(payload),
        abuse_level=normalize_abuse_level(payload),
        estimated_bandwidth=estimated_bandwidth,
        global_latencies=extract_global_latencies(payload),
        cleanliness_score=cleanliness_score,
        tags=tags,
    )


def classify_ip_type_from_payload(payload: dict[str, object], asn: str, isp: str) -> str:
    if payload.get("is_datacenter") or payload.get("company_type") == "hosting":
        return "Datacenter"
    if payload.get("isResidential") or payload.get("asn_kind") in {"residential", "isp", "mobile"}:
        return "Residential-like"
    if payload.get("is_mobile"):
        return "Residential-like"
    return classify_ip_type(asn, isp)


def normalize_operator_type(payload: dict[str, object]) -> str:
    company_type = str(payload.get("company_type") or payload.get("asn_kind") or "").strip().lower()
    if company_type in {"isp", "hosting", "cdn", "business", "mobile", "residential", "mixed"}:
        return company_type
    return "unknown"


def detect_human_traffic(payload: dict[str, object]) -> str:
    if payload.get("is_crawler"):
        return "crawler_heavy"
    return "human_heavy"


def detect_ip_native(payload: dict[str, object]) -> str:
    if payload.get("is_vpn") or payload.get("is_proxy") or payload.get("is_tor"):
        return "non_native"
    if payload.get("isResidential") or payload.get("company_type") == "isp":
        return "native"
    if payload.get("is_datacenter"):
        return "datacenter"
    return "unknown"


def normalize_abuse_level(payload: dict[str, object]) -> str:
    intelligence = payload.get("intelligence")
    raw_level = ""
    if isinstance(intelligence, dict):
        raw_level = str(intelligence.get("abuser_level") or "").strip().lower()
    if not raw_level and payload.get("is_abuser"):
        raw_level = "low"
    if raw_level in {"very_high", "veryhigh"}:
        return "very_high"
    if raw_level in {"high", "elevated", "low"}:
        return raw_level
    return "clean"


def resolve_geo_location(payload: dict[str, object]) -> tuple[str, str]:
    geo_sources = payload.get("geo_sources")
    if isinstance(geo_sources, list):
        ranked_locations: dict[tuple[str, str, str], tuple[int, int]] = {}
        for source in geo_sources:
            if not isinstance(source, dict):
                continue
            country = str(source.get("country") or source.get("registered_country") or "").strip()
            region = str(source.get("region") or "").strip()
            city = str(source.get("city") or "").strip()
            if not country:
                continue
            key = (country, region, city)
            count, quality = ranked_locations.get(key, (0, 0))
            quality += int(bool(region)) + int(bool(city)) + int(source.get("lat") is not None and source.get("lon") is not None)
            ranked_locations[key] = (count + 1, quality)

        if ranked_locations:
            country, region, city = max(
                ranked_locations,
                key=lambda item: (
                    ranked_locations[item][0],
                    ranked_locations[item][1],
                    bool(item[2]),
                    bool(item[1]),
                ),
            )
            location_parts = [part for part in (region, city) if part]
            return country, " / ".join(location_parts) or "Unknown"

    country = str(payload.get("country") or payload.get("country_name") or "Unknown")
    region = str(payload.get("region") or payload.get("city") or payload.get("region_code") or "Unknown")
    return country, region


def fetch_global_latencies(client: httpx.Client, exit_ip: str) -> dict[str, object]:
    node_query = "&".join(f"node={node_id}" for node_id in GLOBAL_PING_NODES)
    try:
        response = client.get(f"https://ip.net.coffee/api/ping/global?host={exit_ip}&{node_query}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    except httpx.HTTPError:
        return {}
    return {}


def extract_global_latencies(payload: dict[str, object]) -> dict[str, str]:
    raw_ping = payload.get("_global_latencies")
    if not isinstance(raw_ping, dict):
        return {}

    results = raw_ping.get("results")
    timeouts = raw_ping.get("timeouts")
    latency_by_region: dict[str, str] = {}
    result_values = results if isinstance(results, dict) else {}
    timeout_values = set(timeouts) if isinstance(timeouts, list) else set()

    for node_id, region_name in GLOBAL_PING_NODES.items():
        if node_id in result_values:
            latency_by_region[region_name] = f"{result_values[node_id]} ms"
        elif node_id in timeout_values:
            latency_by_region[region_name] = "Timeout"

    return latency_by_region


def classify_ip_type(asn: str, isp: str) -> str:
    haystack = f"{asn} {isp}".lower()
    if any(hint in haystack for hint in DATACENTER_HINTS):
        return "Datacenter"
    if re.search(r"\b(residential|broadband|telecom|cable|fiber|mobile)\b", haystack):
        return "Residential-like"
    return "Unknown"


def score_result(result: ProxyCheckResult) -> ProxyCheckResult:
    score = result.cleanliness_score if result.cleanliness_score > 0 else 100
    notes = [*result.notes]

    if not result.reachable:
        score -= 55
    if not result.target_reachable:
        score -= 20
    if result.blocked:
        score -= 25
    if result.captcha:
        score -= 20
    if result.latency_ms is None:
        score -= 10
    elif result.latency_ms > 3000:
        score -= 20
        notes.append("Latency is high.")
    elif result.latency_ms > 1200:
        score -= 10
        notes.append("Latency is moderate.")
    if result.ip_type == "Datacenter":
        score -= 15
    if result.country == "Unknown" or result.asn == "Unknown":
        score -= 10

    score = max(0, min(100, score))
    risk_score = 100 - score

    return replace(
        result,
        cleanliness_score=score,
        risk_score=risk_score,
        notes=notes,
        tags=sorted(set(result.tags)),
    )
