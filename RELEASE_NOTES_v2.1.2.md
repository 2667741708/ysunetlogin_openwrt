# YSU Netlogin Healing Manager 2.1.2

## 修复与改进

- “校园网账号”新增“在线设备查询位置”，默认优先选择 `c201-4090`，并允许修改为本机或其他 SSH 目标。
- 在线设备查询和设备下线操作可在远程服务器上执行，再把结构化结果返回本机界面。
- 修复账号已经在 4090 在线、但查询错误地在本机执行而显示“未检测到 auth1 新认证页面”的问题。
- SSH 远端自动探测 `python3`、`python` 和 `python.exe`，兼容 Linux 与 Windows 服务器。
- 增加 Windows CNG AES 后端；远端 Windows Python 即使没有 PyCryptodome、cryptography 或 OpenSSL，也能完成校园网密码加密。

## 实机验证

- Titan：停止 `fleet-titan` 借网后，使用“博士移动”登录中国移动成功；核对账号、运营商、物理 IP 与公网均成功；随后注销并恢复借网。
- Titan 恢复后：`fleet-titan` 为运行中、自动启动，公网探测返回 HTTP 204。
- 4090：远程查询账号 `202311030027` 成功，复用当前会话并返回设备、IP 与中国移动运营商。
- 23 项自动化测试全部通过。
