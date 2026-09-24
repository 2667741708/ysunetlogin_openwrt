# `netlogin` 系统命令安装

`netlogin` 命令包是一个 Python zipapp，内含以下运行模块：

- `netlogin.py`
- `self_service.py`
- `campus_network.py`
- `wifi_scan.py`
- `campus_adapters.ps1`（Windows 物理网卡检测资源）

这些模块只使用 Python 标准库。服务器需要 Python 3，但用户运行时只需输入
`netlogin`，不需要找到源码目录或手写 `python3 netlogin.py`。

## 构建

```powershell
python .\build_cli_bundle.py
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
