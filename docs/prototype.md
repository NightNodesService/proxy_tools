# proxy_tools prototype

## Product direction

`proxy_tools` is an open-source Windows desktop app for checking whether a single HTTP/HTTPS proxy is usable, clean enough, and able to reach selected e-commerce websites.

The first version intentionally stays lightweight: one proxy input, one selected target website, one check button, and one readable result panel.

## Target users

- Developers testing network tools
- General users checking whether a proxy works
- Operators who need quick visibility into latency and reachability

## First-version decisions

- Open source and free
- Windows first
- Python + PySide6 desktop app
- No account system
- Local configuration files
- Single proxy check only
- HTTP request detection first
- Browser simulation mode is reserved for a later version
- Chinese and English interface support

## Main screens

### Check

The main workbench contains:

- Language selector
- Proxy type selector: HTTP or HTTPS
- Proxy input: `ip:port:user:pass`
- Detection mode selector: Lightweight HTTP request or browser simulation placeholder
- E-commerce target selector
- Start check button
- Score summary
- Connectivity, latency, IP info, website result, block hint, captcha hint, and notes

### Target Sites

The target list page contains:

- Website name
- URL
- Category
- Enabled state

The first prototype loads targets from `config/targets.json`.

### Settings

The settings page contains:

- Timeout seconds
- User-Agent
- Local config path hint

## Cleanliness model

The prototype score starts at 100 and subtracts points for common problems:

- Proxy connection failure
- Target website timeout or failure
- HTTP 403, 429, or similar block status
- Captcha or robot-check text hints
- High latency
- Missing IP intelligence data
- Datacenter or hosting ASN hints

The first version is a heuristic score. Future versions can integrate optional risk-scoring APIs.

## Later roadmap

- Batch proxy import
- CSV/JSON export
- Browser simulation mode
- More IP intelligence providers
- Configurable scoring rules
- Proxy history
- Windows executable packaging
