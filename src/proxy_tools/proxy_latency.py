from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

import httpx

from .checker import normalize_proxy
from .models import AppSettings, TargetSite


@dataclass
class ProxyLatencyResult:
    proxy: str
    target_name: str
    target_url: str
    attempts: int
    success_count: int = 0
    median_ms: int | None = None
    average_ms: int | None = None
    min_ms: int | None = None
    max_ms: int | None = None
    status_codes: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> int:
        if self.attempts <= 0:
            return 0
        return round(self.success_count / self.attempts * 100)

    @property
    def status_label(self) -> str:
        if self.success_count == self.attempts and self.attempts > 0:
            return "Passed"
        if self.success_count > 0:
            return "Partial"
        return "Failed"


def run_proxy_latency_tests(
    proxies: list[str],
    proxy_type: str,
    target: TargetSite,
    settings: AppSettings,
    attempts: int,
) -> list[ProxyLatencyResult]:
    normalized_attempts = max(1, min(5, attempts))
    results: list[ProxyLatencyResult] = []
    for raw_proxy in unique_proxies(proxies):
        results.append(
            test_proxy_latency(
                raw_proxy=raw_proxy,
                proxy_type=proxy_type,
                target=target,
                settings=settings,
                attempts=normalized_attempts,
            )
        )
    return results


def test_proxy_latency(
    raw_proxy: str,
    proxy_type: str,
    target: TargetSite,
    settings: AppSettings,
    attempts: int,
) -> ProxyLatencyResult:
    proxy = raw_proxy.strip()
    result = ProxyLatencyResult(
        proxy=proxy,
        target_name=target.name,
        target_url=target.url,
        attempts=attempts,
    )

    try:
        proxy_url = normalize_proxy(proxy, proxy_type)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result

    latencies: list[int] = []
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(settings.timeout_seconds)

    for index in range(attempts):
        try:
            with httpx.Client(
                proxy=proxy_url,
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                started = time.perf_counter()
                response = client.get(target.url)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                latencies.append(elapsed_ms)
                result.success_count += 1
                result.status_codes.append(response.status_code)
        except httpx.TimeoutException:
            result.errors.append("Request timed out.")
        except httpx.ProxyError as exc:
            result.errors.append(f"Proxy error: {exc}")
        except httpx.HTTPError as exc:
            result.errors.append(f"HTTP error: {exc}")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Unexpected error: {exc}")

        if index < attempts - 1:
            time.sleep(0.15)

    if latencies:
        result.median_ms = round(statistics.median(latencies))
        result.average_ms = round(statistics.fmean(latencies))
        result.min_ms = min(latencies)
        result.max_ms = max(latencies)

    return result


def unique_proxies(proxies: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for proxy in proxies:
        value = proxy.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
