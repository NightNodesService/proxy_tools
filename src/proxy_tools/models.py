from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TargetSite:
    name: str
    url: str
    category: str
    enabled: bool = True


@dataclass(frozen=True)
class AppSettings:
    timeout_seconds: float = 12.0
    language: str = "zh"
    theme: str = "tech_dark"
    local_chrome_test: bool = False
    ipinfo_token: str = ""
    proxycheck_key: str = ""
    abuseipdb_key: str = ""
    ipqualityscore_key: str = ""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )


@dataclass
class ProxyCheckResult:
    proxy: str
    proxy_type: str
    target_name: str
    target_url: str
    mode: str
    reachable: bool = False
    target_reachable: bool = False
    latency_ms: int | None = None
    exit_ip: str = "Unknown"
    country: str = "Unknown"
    region: str = "Unknown"
    coordinates: str = "Unknown"
    asn: str = "Unknown"
    isp: str = "Unknown"
    ip_type: str = "Unknown"
    company_info: str = "Unknown"
    operator_type: str = "Unknown"
    human_traffic: str = "unknown"
    ip_native: str = "unknown"
    abuse_level: str = "unknown"
    estimated_bandwidth: str = "Unknown"
    global_latencies: dict[str, str] = field(default_factory=dict)
    risk_score: int = 50
    cleanliness_score: int = 0
    blocked: bool = False
    captcha: bool = False
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        if not self.reachable:
            return "Proxy failed"
        if not self.target_reachable:
            return "Target failed"
        if self.blocked or self.captcha:
            return "Needs review"
        return "Passed"
