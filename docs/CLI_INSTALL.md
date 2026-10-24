# 单文件安装与依赖

## 普通用户

从 [下载入口](https://github.com/2667741708/ysunetlogin_openwrt/releases/latest/download/netlogin.pyz) 保存 netlogin.pyz，不需要解压或下载其他项目文件：

```bash
python netlogin.pyz login
```

交互输入账号、密码和运营商；Linux 可使用 python3。认证成功后自动维护连接。可选校验文件 SHA256SUMS.txt 在同一发布页：Windows 用 Get-FileHash，Linux 用 sha256sum。

Python 3 是运行环境。认证需要 AES：Windows 可使用系统 CNG；Linux 可使用系统 OpenSSL，或安装 Python cryptography / PyCryptodome。只有需要测试或构建时才安装 requirements-dev.txt。

## 完整源码

不要只下载 netlogin.py。完整解压仓库源码后，保持根目录模块及 campus_adapters.ps1 在一起，执行 python netlogin.py login。根目录业务模块被拆分是为了维护；单文件发布包已封装这些模块。

## 开发者构建与系统命令安装

`netlogin` 命令包是一个 Python zipapp，内含以下运行模块：

- `netlogin.py`
- `auto_heartbeat.py`
- `heartbeat.py`
- `self_service.py`
- `campus_network.py`
- `wifi_scan.py`
- `campus_adapters.ps1`（Windows 物理网卡检测资源）

这些模块只使用 Python 标准库。服务器需要 Python 3，但用户运行时只需输入
`netlogin`，不需要找到源码目录或手写 `python3 netlogin.py`。

## 构建

```powershell
python .\scripts\build_cli_bundle.py
python .\release\netlogin.pyz --version
python .\release\netlogin.pyz --help
```

构建程序会输出 SHA-256。部署时应先上传到临时文件，验证版本和摘要后再替换旧命令。

## Linux

有管理员权限时安装为：

```text
/usr/local/bin/netlogin
```

普通用户安装为：

```text
~/.local/bin/netlogin
```

文件必须具有可执行权限。Ubuntu 的登录环境通常已经将 `~/.local/bin` 加入
`PATH`；若当前终端尚未刷新，可重新登录 SSH。

## Windows

推荐保存包和启动器：

```text
C:\ProgramData\YSUNetLogin\netlogin.pyz
C:\Windows\System32\netlogin.cmd
```

`netlogin.cmd` 负责调用机器上已验证的 Python 3。安装完成后，在 PowerShell、
CMD 或 SSH 会话的任何目录均可直接执行：

```text
netlogin --version
netlogin --help
netlogin current-status --json
netlogin query-machine-status
```

账号密码优先通过 `login-stdin`、交互输入或受保护账号文件传递，不建议把密码直接
写在命令历史中。

## 登录后自动维护

新命令包在登录成功后自动启动独立后台心跳，无需 cron、开始/结束时段或手动开启。每 60 秒检查，连续异常 2 次后重连并验证，持续失败最长退避 15 分钟。关闭 SSH 后继续运行；机器重启后需要下一次登录重新启动。

```text
netlogin heartbeat-status
netlogin logout
```

logout 同时停止维护、删除重连凭据并下线。只想停维护用 heartbeat-stop；高级场景的一次性认证可给登录命令追加 --once。Linux 配置仅当前用户可读，Windows 密码使用当前用户 DPAPI 加密；运行状态不输出密码。
