# LAN Web GUI（高级入口）

`lan_gui.py` 是一个仅使用 Python 标准库的集中控制台。它在本机通过固定 SSH
白名单访问 4090、5080 或其他已配置的校园网服务器，并只允许调用远端
`netlogin.py login-stdin/current-status`，不提供任意命令执行入口。

准备配置：

```powershell
Copy-Item .\examples/lan_gui_config.example.json .\lan_gui_config.json
```

编辑 `lan_gui_config.json`，为每台服务器填写：

- `ssh_target`：本机已经可以免交互登录的 SSH 别名或 `user@ip`
- `expected_hostname`：远端 `hostname` 的精确结果，用于防止连错机器
- `script`：远端 `netlogin.py` 的绝对路径

LAN 模式默认要求 HTTPS。首次部署可用 OpenSSL 生成本地证书：

```bash
openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
  -keyout lan_gui.key -out lan_gui.crt -subj "/CN=你的GUI主机名"
```

Windows 启动：

```powershell
.\scripts/start_lan_gui.ps1 -Password '至少10位的GUI访问密码'
```

Linux 启动：

```bash
chmod 700 scripts/start_lan_gui.sh
./scripts/start_lan_gui.sh '至少10位的GUI访问密码'
```

随后在同一 LAN 的浏览器打开 `https://GUI主机IP:8765/`。自签名证书需要由管理员
将 `lan_gui.crt` 导入客户端信任库；正式多人环境建议换成内网 CA 签发的证书。
校园网账号密码只用于当前请求，经 HTTPS 到 GUI 后再通过 SSH 标准输入传给目标服务器，
不会写入 GUI 配置、Cookie 或日志。

远端使用最新完整源码时，login-stdin 登录成功会自动启动该远端用户的后台心跳，不需要 daemon.sh 或 cron。维护凭据按远端当前用户权限保存，使用远端 logout 停止并删除。旧远端脚本的行为以其实际版本为准。
