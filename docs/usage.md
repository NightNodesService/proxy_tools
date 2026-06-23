# proxy_tools Usage Guide / proxy_tools 使用说明

## English

### 1. Start The App

For the Windows build, open:

```text
dist/proxy_tools/proxy_tools_v0.2.0-beta.exe
```

Keep the full `dist/proxy_tools` folder. The app needs the included runtime, configuration, and assets.

### 2. Proxy Check

Use **Proxy Check** to inspect one proxy.

1. Select proxy type: `HTTP` or `HTTPS`.
2. Enter proxy in `ip:port:user:pass` format.
3. Select detection mode:
   - Lightweight HTTP: fast request-based check.
   - Browser Simulation: uses browser-style access checks.
4. Select a target website.
5. Click **Run Audit**.

The report shows connectivity, cleanliness score, risk score, latency, exit IP, country/region, ASN/ISP, IP type, target reachability, block hints, captcha hints, and raw notes.

### 3. Proxy Tests

Use **Proxy Tests** to test multiple proxies against one target website.

1. Select proxy type.
2. Set attempts from `1` to `5`.
3. Set concurrency from `1` to `20`.
4. Select target website.
5. Paste proxies into the proxy list, one proxy per line.
6. Click **Start Test**.

All proxies are added to the result table immediately. Each row updates when its test finishes. After the full batch completes, the HTTP Status cell shows a refresh icon for per-proxy retry.

Double-click any result cell to copy its value.

### 4. Target Sites

Use **Target Sites** to manage websites used by proxy checks and latency tests.

You can add, update, delete, enable, or disable websites. Changes sync to the target selectors.

### 5. Third-Party Checks

Use **Third-Party Checks** to query IP risk providers.

Supported providers include:

- proxycheck.io
- IPinfo
- Scamalytics
- AbuseIPDB
- IPQualityScore

Some providers may require API keys in **Settings**.

### 6. Settings

Settings are local only.

You can configure:

- Timeout seconds
- Interface language
- Theme
- User-Agent
- Third-party API keys

Supported languages:

- Chinese
- English
- Japanese
- Korean
- Vietnamese

## 中文

### 1. 启动 APP

Windows 成品请打开：

```text
dist/proxy_tools/proxy_tools_v0.2.0-beta.exe
```

请保留完整的 `dist/proxy_tools` 文件夹。APP 运行需要其中的运行时依赖、配置文件和资源文件。

### 2. 代理检测

使用 **代理检测** 页面检测单条代理。

1. 选择代理类型：`HTTP` 或 `HTTPS`。
2. 输入代理，格式为 `ip:port:user:pass`。
3. 选择检测模式：
   - 轻量 HTTP：基于请求的快速检测。
   - 浏览器模拟：使用更接近浏览器访问的检测方式。
4. 选择目标网站。
5. 点击 **开始体检**。

报告会展示连通性、纯净度评分、风险评分、延迟、出口 IP、国家/地区、ASN/ISP、IP 类型、目标网站可达性、拦截信号、验证码信号和原始说明。

### 3. 代理测试

使用 **代理测试** 页面批量测试多条代理访问某个目标网站的延迟。

1. 选择代理类型。
2. 设置测试次数，范围 `1-5`。
3. 设置并发数，范围 `1-20`。
4. 选择目标网站。
5. 在代理列表中粘贴代理，每行一条。
6. 点击 **开始测试**。

开始后，所有代理会立即进入结果列表。每条代理测试完成后会实时更新对应行。整批测试完成后，HTTP 状态列会显示刷新图标，可对单条代理重试。

双击结果列表中的任意单元格，可复制该单元格内容。

### 4. 网址列表

使用 **网址列表** 管理代理检测和代理测试使用的目标网站。

你可以新增、更新、删除、启用或禁用网站。修改会同步到目标网站下拉框。

### 5. 第三方检测

使用 **第三方检测** 查询 IP 风险来源。

支持的来源包括：

- proxycheck.io
- IPinfo
- Scamalytics
- AbuseIPDB
- IPQualityScore

部分来源需要在 **设置** 中填写 API Key。

### 6. 设置

设置仅保存在本地。

可配置内容包括：

- 超时时间
- 界面语言
- 主题
- User-Agent
- 第三方 API Key

支持语言：

- 中文
- 英文
- 日文
- 韩文
- 越南语
