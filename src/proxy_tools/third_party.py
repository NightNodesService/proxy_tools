from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from html import unescape

import httpx

from .models import AppSettings


@dataclass
class ThirdPartyResult:
    provider: str
    status: str
    risk: str = "--"
    proxy: str = "--"
    country: str = "--"
    asn: str = "--"
    summary: str = ""


PROVIDERS = [
    "proxycheck.io",
    "IPinfo Lite",
    "Scamalytics",
    "AbuseIPDB",
    "IPQualityScore",
]


def validate_ip(value: str) -> str:
    return str(ipaddress.ip_address(value.strip()))


def run_third_party_checks(ip: str, providers: list[str], settings: AppSettings) -> list[ThirdPartyResult]:
    target_ip = validate_ip(ip)
    results: list[ThirdPartyResult] = []
    for provider in providers:
        try:
            if provider == "IPinfo Lite":
                results.append(check_ipinfo(target_ip, settings))
            elif provider == "proxycheck.io":
                results.append(check_proxycheck(target_ip, settings))
            elif provider == "Scamalytics":
                results.append(check_scamalytics(target_ip, settings))
            elif provider == "AbuseIPDB":
                results.append(check_abuseipdb(target_ip, settings))
            elif provider == "IPQualityScore":
                results.append(check_ipqualityscore(target_ip, settings))
        except Exception as exc:  # noqa: BLE001
            results.append(ThirdPartyResult(provider=provider, status="Error", summary=str(exc)))
    return results


def check_ipinfo(ip: str, settings: AppSettings) -> ThirdPartyResult:
    if not settings.ipinfo_token:
        return ThirdPartyResult(
            provider="IPinfo Lite",
            status="Need token",
            summary="IPinfo Lite requires a token. Configure it in Settings.",
        )

    url = f"https://api.ipinfo.io/lite/{ip}"
    payload = request_json(url, params={"token": settings.ipinfo_token}, timeout=settings.timeout_seconds)
    asn = str(payload.get("asn") or payload.get("as_name") or "--")
    country = str(payload.get("country") or payload.get("country_code") or "--")
    org = str(payload.get("as_name") or payload.get("as_domain") or "")
    return ThirdPartyResult(
        provider="IPinfo Lite",
        status="OK",
        country=country,
        asn=asn,
        summary=org,
    )


def check_proxycheck(ip: str, settings: AppSettings) -> ThirdPartyResult:
    params = {"vpn": "1", "asn": "1", "risk": "1", "node": "1", "time": "1"}
    if settings.proxycheck_key:
        params["key"] = settings.proxycheck_key

    payload = request_json(f"https://proxycheck.io/v2/{ip}", params=params, timeout=settings.timeout_seconds)
    item = payload.get(ip, {}) if isinstance(payload.get(ip), dict) else {}
    proxy = str(item.get("proxy", "--"))
    risk = str(item.get("risk", "--"))
    country = str(item.get("country", item.get("isocode", "--")))
    asn = str(item.get("asn", "--"))
    provider = str(item.get("provider", ""))
    status = str(payload.get("status", "OK"))
    return ThirdPartyResult(
        provider="proxycheck.io",
        status=status,
        risk=risk,
        proxy=proxy,
        country=country,
        asn=asn,
        summary=provider,
    )


def check_scamalytics(ip: str, settings: AppSettings) -> ThirdPartyResult:
    url = f"https://scamalytics.com/ip/{ip}"
    with httpx.Client(timeout=settings.timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            url,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if response.status_code == 403:
            return ThirdPartyResult(
                provider="Scamalytics",
                status="Web blocked",
                summary=f"Public lookup blocks this client. Open manually: {url}",
            )
        response.raise_for_status()
    text = unescape(re.sub(r"\s+", " ", response.text))
    score = find_first(text, [r"Fraud Score:\s*(\d+)", r"Fraud score[^0-9]*(\d+)"])
    proxy = find_first(text, [r"Proxy[^A-Za-z0-9]+(Yes|No)", r"Anonymizing VPN[^A-Za-z0-9]+(Yes|No)"])
    country = find_first(text, [r"Country Name[^A-Za-z]+([A-Za-z ,.-]+?)\s{2,}", r"Country[^A-Za-z]+([A-Za-z ,.-]+?)\s{2,}"])
    return ThirdPartyResult(
        provider="Scamalytics",
        status="OK",
        risk=score or "--",
        proxy=proxy or "--",
        country=(country or "--").strip(),
        summary="Public web lookup parsed on a best-effort basis.",
    )


def check_abuseipdb(ip: str, settings: AppSettings) -> ThirdPartyResult:
    if not settings.abuseipdb_key:
        return ThirdPartyResult(
            provider="AbuseIPDB",
            status="Need API key",
            summary="Configure an AbuseIPDB API key in Settings.",
        )

    payload = request_json(
        "https://api.abuseipdb.com/api/v2/check",
        params={"ipAddress": ip, "maxAgeInDays": "90", "verbose": ""},
        headers={"Key": settings.abuseipdb_key, "Accept": "application/json"},
        timeout=settings.timeout_seconds,
    )
    data = payload.get("data", {})
    return ThirdPartyResult(
        provider="AbuseIPDB",
        status="OK",
        risk=str(data.get("abuseConfidenceScore", "--")),
        country=str(data.get("countryCode", "--")),
        summary=f"reports={data.get('totalReports', '--')} usage={data.get('usageType', '--')}",
    )


def check_ipqualityscore(ip: str, settings: AppSettings) -> ThirdPartyResult:
    if not settings.ipqualityscore_key:
        return ThirdPartyResult(
            provider="IPQualityScore",
            status="Need API key",
            summary="Configure an IPQualityScore API key in Settings.",
        )

    payload = request_json(
        f"https://ipqualityscore.com/api/json/ip/{settings.ipqualityscore_key}/{ip}",
        params={"strictness": "1", "allow_public_access_points": "true"},
        timeout=settings.timeout_seconds,
    )
    return ThirdPartyResult(
        provider="IPQualityScore",
        status="OK" if payload.get("success", True) else "Error",
        risk=str(payload.get("fraud_score", "--")),
        proxy=str(payload.get("proxy", "--")),
        country=str(payload.get("country_code", "--")),
        asn=str(payload.get("ASN", "--")),
        summary=f"vpn={payload.get('vpn', '--')} tor={payload.get('tor', '--')} isp={payload.get('ISP', '--')}",
    )


def request_json(
    url: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 12.0,
) -> dict[str, object]:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Provider did not return a JSON object.")
    return payload


def find_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
