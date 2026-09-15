# 校园网直连、无线扫描与指定运营商

## 本次修复

1. 指定移动、联通、电信时，不会自动改选校园网。没有唯一匹配服务会报错。
2. 外网可达不再被当作校园认证成功；登录后必须核实账号和实际运营商。
3. Windows 自动检测物理有线、无线网卡，通过每张网卡自己的 DNS 查询认证地址，
   并将 DNS、HTTP、HTTPS 连接绑定到该网卡的接口编号和本机 IPv4。
4. 先直连 auth1 认证入口获取校园会话，不依赖外网探测页面来决定能否登录。
5. 默认主动扫描附近 Wi-Fi，标出 iYanDa 是否可见；扫描失败不会阻止有线检测。

检测不会修改系统代理开关、路由表、DNS 设置或自动切换 Wi-Fi。
多张网卡检测通过时，优先选择有线；有线不通时选择检测通过的无线网卡。
全部失败则停止，不退回未经核验的默认出口。

## 安装

将下列文件放在同一目录，或直接解压本次提供的源码包：

- `netlogin.py`
- `campus_network.py`
- `campus_adapters.ps1`
- `wifi_scan.py`
- `campus_network.example.json`（可选配置模板）

Windows 建议使用 Python 3。此更新有新增依赖文件，不能只复制 netlogin.py。
Linux / OpenWrt 不使用 Windows 网卡绑定模块，保留原有直连认证流程。

## 命令

在上述文件所在目录执行：

```text
python netlogin.py wifi-scan
python netlogin.py campus-check
python netlogin.py campus-check --json
python netlogin.py current-status
python netlogin.py "学号" "密码" 1
```

运营商编号：0 校园网，1 移动，2 联通，3 电信。
命令行如果发现已在线账号或服务不符，会要求先执行 `python netlogin.py logout`。
`login-stdin` 和桌面端的显式连接保留自动切换旧会话的行为。
`--status`、`--current-status` 也可用。文件名必须包含 `.py`。

## 可配置项目

默认自动扫描和网卡检查已开启。需要调整时，将 `campus_network.example.json`
复制为同目录的 `campus_network.json`，例如：

```powershell
Copy-Item -LiteralPath .\campus_network.example.json -Destination .\campus_network.json
```

已有配置时直接编辑，不必再次复制模板。示例内容：

```json
{
  "scan_wifi": true,
  "preferred_ssid": "iYanDa",
  "interface": "auto",
  "timeout": 4
}
```

- `scan_wifi`：是否主动扫描附近 Wi-Fi。设为 false 仍会检查有线和已连接的无线网卡。
- `preferred_ssid`：要在扫描结果中标识的网络，不会仅凭名称自动连接。
- `interface`：auto 自动检测；也可以填准确网卡名称，例如 WLAN 或“以太网 2”。
- `timeout`：每次网络请求超时，范围 1 至 10 秒。
- 可用环境变量 `YSUNETLOGIN_NETWORK_CONFIG` 指定配置文件路径。
- 后续打包的 EXE 从可执行文件旁读取配置文件；旧 EXE 需重新构建后才包含本次修改。

关闭扫描但继续检查校园网卡，可将 `scan_wifi` 设为 `false`。如果只想检查有线，
将 `interface` 改成系统显示的有线网卡名，例如 `以太网 2`；无线则可指定 `WLAN`。
指定网卡不通时会报错，不自动使用其他网卡。

PowerShell 中使用其他配置文件的示例：

```powershell
$env:YSUNETLOGIN_NETWORK_CONFIG = 'D:\Netlogin\campus_network.json'
python netlogin.py campus-check --json
```

### JSON 登录输入

程序或桌面端可以把下面格式的 JSON 经标准输入传给 `python netlogin.py login-stdin`：

```json
{"username": "学号", "password": "密码", "service": "1"}
```

`service` 必须是字符串 `0`、`1`、`2` 或 `3`。该入口会核实当前账号和运营商，
显式连接时可自动下线不匹配的旧会话。不要把含真实密码的 JSON 加入 Git。

## 验证范围

本机 iYanDa / WLAN 已在系统代理开启时通过网卡绑定直连检测，并读到中国移动在线。
有线网卡当前未接线，其实网登录尚未验证。
物理网卡绑定可避免依赖默认路由和系统 DNS，但代理的过滤驱动仍可能拦截流量；
遇到这种情况程序会报告直连失败，不会宣称已经绕过所有 TUN / VPN 驱动。
