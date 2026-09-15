# 更新日志

## 2026-09-15 — 指定运营商、校园网卡直连与 Wi-Fi 扫描

### 修复

- 指定移动、联通、电信时，只选择唯一匹配的服务；找不到或匹配不明确时停止，
  展示服务器可用服务，不再静默选择校园网或列表第一项。
- 登录成功必须核实在线账号和实际运营商，接口未返回服务或服务不一致时不报告成功。
- 修复外网可达时跳过校园认证的判断；校园认证状态优先于普通外网探测结果。
- 优先直连 `auth1.ysu.edu.cn` 获取校园会话，减少对公共网站重定向探测的依赖。
- 兼容 `--status`、`--current-status`；帮助命令不再执行空账号登录，登录失败返回非零退出码。

### 新增

- Windows 物理网卡检测：分别检查有线与无线，经验证可直连校园认证后选择出口；
  多个出口通过时优先有线，全部失败时停止。
- DNS、HTTP、HTTPS 请求绑定选定网卡及其本机 IPv4；DNS 使用该网卡的服务器，
  不回退到系统 DNS 或未经验证的默认出口，拒绝代理虚拟 IPv4 地址。
- `wifi-scan` 主动扫描并等待完成通知，显示实际可见 Wi-Fi、信号和连接状态。
- `campus-check` 检查附近 `iYanDa`、物理网卡和校园认证直连；支持 JSON 输出。
- `campus_network.example.json` 提供扫描开关、目标 SSID、网卡选择和超时配置。
- 桌面端构建脚本包含新增的网卡检测资源；旧 EXE 不会自动获得源码更新。

### 升级与使用

Windows 升级需一起保留 `netlogin.py`、`campus_network.py`、`campus_adapters.ps1`
和 `wifi_scan.py`。从仓库下载 ZIP 并完整解压即可，不能只替换单个脚本。

```text
python netlogin.py campus-check
python netlogin.py wifi-scan
python netlogin.py current-status
python netlogin.py "学号" "密码" 1
```

详细命令、配置字段与故障处理见 [校园网直连使用说明](CAMPUS_DIRECT_SETUP.md)。
本节记录源码更新；未创建新的 GitHub Release，也未发布新版 EXE。

### 验证与范围

- 74 项自动测试通过，覆盖运营商选择、代理绕过、DNS 校验、物理网卡绑定与出口选择。
- 本机 `iYanDa` 在系统代理开启时通过校园直连检测，并核实中国移动在线。
- 测试进程设置不可用代理、禁用系统 DNS 查询后，仍通过物理网卡查询到中国移动在线。
- 有线网卡未接线，只完成模拟测试；TUN 过滤模式尚未完成实网验证。
- 不自动切换 Wi-Fi，不修改系统代理、DNS 或路由表。扫描失败不会阻止正常有线检查。
- Linux / OpenWrt 保留原有直连认证路径，不启用 Windows 网卡绑定模块。

## 2.1.4

系统代理绕过与显式连接自动切换旧会话，见 [2.1.4 更新说明](RELEASE_NOTES_v2.1.4.md)。
