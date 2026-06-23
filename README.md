# proxy_tools

proxy_tools is a lightweight desktop app for proxy quality inspection, proxy latency testing, and third-party IP risk checks. It is built for developers, proxy operators, and general users who need a fast way to understand whether a proxy can reach target e-commerce websites and whether the exit IP looks risky.

proxy_tools 是一款轻量桌面应用，用于代理质量体检、代理延迟测试和第三方 IP 风险检测。它适合开发者、代理服务运营者，以及需要快速判断代理是否可用、是否能访问目标电商网站、出口 IP 是否存在风险的普通用户。

## Key Features / 主要功能

- HTTP/HTTPS proxy support with `ip:port:user:pass` format.
- Single proxy quality audit: connectivity, exit IP profile, latency, block/captcha hints, and cleanliness score.
- Batch proxy latency test: multiple proxies, target website selection, attempts, concurrency up to 20, live row updates, per-row retry, and copy-on-double-click.
- Target site manager: add, edit, delete, enable, or disable websites used by checks.
- Third-party IP checks: proxycheck.io, IPinfo, Scamalytics, AbuseIPDB, and IPQualityScore integrations.
- Browser simulation mode with local Chrome proxy binding for realistic site access checks.
- Local configuration only; no account system.
- Interface languages: Chinese, English, Japanese, Korean, and Vietnamese.
- Tech Dark and Bright White themes.

- 支持 HTTP/HTTPS 代理，格式为 `ip:port:user:pass`。
- 单条代理质量体检：连通性、出口 IP 信息、延迟、拦截/验证码信号和纯净度评分。
- 批量代理延迟测试：支持多条代理、目标网址选择、测试次数、最高 20 并发、实时逐行更新、单行重试、双击复制。
- 网址列表管理：添加、编辑、删除、启用或禁用检测目标网址。
- 第三方 IP 检测：集成 proxycheck.io、IPinfo、Scamalytics、AbuseIPDB 和 IPQualityScore。
- 浏览器模拟模式：可打开本地 Chrome 并绑定当前代理进行真实访问测试。
- 仅使用本地配置文件，不需要账号系统。
- 界面语言：中文、英文、日文、韩文、越南语。
- 支持科技黑和明亮白主题。

## Downloaded Windows Build / Windows 成品使用

The current Windows build is packaged in onedir mode. Keep the whole folder:

当前 Windows 版本为 onedir 打包方式，请保留整个目录：

```text
dist/proxy_tools/
```

Run:

运行：

```text
dist/proxy_tools/proxy_tools_v0.2.0-beta.exe
```

Do not copy only the `.exe` file. The `_internal`, `config`, and `assets` folders are required.

不要只复制单独的 `.exe` 文件。运行时需要 `_internal`、`config`、`assets` 等目录。

## Run From Source / 从源码运行

```powershell
cd D:\workspace\proxy
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
proxy-tools
```

Or:

或者：

```powershell
python -m proxy_tools
```

## Build Windows / 构建 Windows 版本

```powershell
cd D:\workspace\proxy
.\scripts\build_exe.ps1
```

Output:

输出：

```text
dist/proxy_tools/proxy_tools_v0.2.0-beta.exe
```

## Build macOS / 构建 macOS 版本

macOS apps must be built on macOS. Windows cannot directly generate a working `.app` or `.dmg`.

macOS 应用必须在 macOS 系统上构建。Windows 不能直接生成可运行的 `.app` 或 `.dmg`。

On macOS:

在 macOS 上执行：

```bash
cd proxy_tools
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
```

GitHub Actions workflow is also included at:

仓库内也提供了 GitHub Actions 构建流程：

```text
.github/workflows/build-macos.yml
```

## Documentation / 文档

See the bilingual usage guide:

查看双语使用说明：

```text
docs/usage.md
```

## Project Layout / 项目结构

```text
scripts/
  build_exe.ps1           Windows build script
  build_macos.sh          macOS build script
src/proxy_tools/
  __main__.py             App entry point
  app.py                  PySide6 desktop UI
  checker.py              Single proxy quality check
  proxy_latency.py        Batch proxy latency test
  browser_checker.py      Browser simulation and local proxy bridge
  config.py               Local config loading/saving
  i18n.py                 Multilingual interface text
  third_party.py          Third-party IP risk providers
  models.py               Shared dataclasses
config/
  targets.json            Default target website list
docs/
  usage.md                Bilingual app usage guide
```
