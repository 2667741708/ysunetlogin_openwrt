# 燕山大学校园网 OpenWrt 自动认证方案

Windows 自愈版的下载、SSH、账号绑定、心跳与开机启动配置见：
[Windows 本机使用与配置](WINDOWS_LOCAL_SETUP.md)

这个项目提供了一个为燕山大学校园网设计的 OpenWrt 自动认证解决方案。它能够自动处理校园网的认证过程，使您的设备保持持续在线状态。

## 功能特性

- 自动进行校园网认证
- 持续监控网络状态，在需要时重新认证
- 处理认证失败情况，包括重启网络适配器和路由器
- 智能处理学校自动断网时间
- 记录认证过程和结果
- 自动管理日志文件大小
- 通过修改 TTL 值来绕过校园网多设备限制

## 2026-06 校园网认证改版说明

2026 年 6 月测试时发现，燕山大学校园网认证入口已经不再只是旧版 `auth.ysu.edu.cn/eportal/InterFace.do` 接口。`auth.ysu.edu.cn` 仍然解析到 `10.11.0.1`，端口也能连通，但未认证设备会被新的认证页引导到 `auth1.ysu.edu.cn`，并进入 CAS SSO + portal 工作流。

因此旧版本脚本会出现两类问题：

- 直接访问旧接口时可能返回“用户名或者密码中存在不可用的特殊字符”等误导性错误，尤其是密码包含 `@` 等字符时。
- 认证成功不再等于网络放通。新流程在账号密码认证后，还会进入 `serviceSelection` 节点，需要选择“校园网 / 中国移动 / 中国联通 / 中国电信”等服务并调用新的 `serviceLogin` 接口。

本次修复做了这些兼容：

- 先探测外网地址并捕获 `auth1.ysu.edu.cn` 的新 portal 跳转。
- 按新页面要求获取 CAS 登录参数，并用 AES-ECB-PKCS7 加密密码。
- 登录成功后继续调用 portal 工作流，处理 `serviceSelection` 并选择配置的运营商服务。
- 保留旧 `InterFace.do` 登录逻辑作为兜底，方便旧环境继续使用。
- 为网络探测增加超时和禁止自动重定向，避免被 `123.123.123.123` 等 captive portal 地址卡住。

如果你的网络环境仍然使用旧认证接口，脚本会自动回退；如果已经升级到新 portal，则会优先走新流程。

## 安装指南

1. 将 `netlogin.py`、`auth.sh` 和 `daemon.sh` 文件上传到您的 OpenWrt 设备的 `/etc/storage/` 目录。

2. 确保您的 OpenWrt 设备已安装 Python。新认证流程需要 AES 加密，脚本会优先使用 Python 的 `Crypto.Cipher.AES`，如果没有安装该库，则会调用系统 `openssl` 命令完成加密。

3. 给予脚本执行权限：
   ```
   chmod +x /etc/storage/auth.sh /etc/storage/daemon.sh
   ```

## 配置说明

### 配置 auth.sh

1. 打开 `auth.sh` 文件进行编辑：
   ```
   vi /etc/storage/auth.sh
   ```

2. 在文件中找到以下行，并替换相应的信息：
   ```bash
   result=$(python "/etc/storage/netlogin.py" "你的学号" "你的校园网密码" "运营商编号")
   ```
   
   将 "你的学号" 替换为您的学号，"你的校园网密码" 替换为您的校园网密码，"运营商编号" 替换为相应的数字：
   - 0: 校园网
   - 1: 中国移动
   - 2: 中国联通
   - 3: 中国电信

3. 保存并退出文件。
4. 如果您的 OpenWrt 设备重置网络适配器的方式不一致（如 18.06 以上的高版本系统），可能需要您重新编写一部分脚本。

### 配置 daemon.sh

1. 打开 `daemon.sh` 文件进行编辑：
   ```
   vi /etc/storage/daemon.sh
   ```

2. 根据需要调整以下参数：
   - `start_hour` 和 `end_hour`：设置学校自动断网的时间段（默认配置即已适合燕山大学校园网环境）
   - `max_attempts`：设置最大重试次数

3. 配置其中含绝对路径的脚本文件路径（可选）：
   ```shell
   python /etc/storage/netlogin.py logout
   /etc/storage/auth.sh # 将此处和上方替换为你认证脚本的位置
   ```

4. 保存并退出文件。

### 设置定时任务

为了确保脚本定期运行，我们需要设置 cron 任务：

1. 打开 crontab 进行编辑：
   ```
   crontab -e
   ```

2. 添加以下行来每1分钟运行一次 daemon 脚本：
   ```
   */1 * * * * /etc/storage/daemon.sh
   ```

3. 保存并退出。

## 使用方法

### Windows 桌面 EXE（推荐）

双击 `dist-secure/YSU-Netlogin-Healing-Manager.exe` 即可使用，不需要另外安装 Python，也不需要记住
远端 `netlogin.py` 的目录。桌面程序主要功能包括：

- **一键连接**：选择“本机 Windows”或 SSH 服务器后自动带出默认校园网账号，点击一次完成认证。
  也可以查询当前状态，或让所选目标当前校园网会话下线，再切换账号/运营商重新登录。
- **内置 VNC**：选择 SSH 主机后点击“打开 VNC 桌面”，程序自动建立严格校验的 SSH 隧道并启动 EXE 内置的 TigerVNC Viewer；无需用户另外安装 Viewer 或记忆端口。
- **校园网账号**：保存多个账号、密码和默认运营商。密码通过 Windows DPAPI 加密，
  只能由保存它们的当前 Windows 用户解密。账号页可以查询该账号当前在线设备，并按
  设备或全部设备执行下线。
- **连接目标**：内置不可删除的“本机 Windows”，并可只读导入当前 Windows 用户 `~/.ssh/config` 中的 `Host` 别名；
  GUI 只保存连接别名、预期主机名、可选的远端 `netlogin.py` 路径和默认校园网账号。
  它不会读取、保存或修改 `IdentityFile`，不会写回 SSH config，也不会打包任何 SSH
  私钥。SSH 身份认证完全由 Windows OpenSSH、现有 SSH config 或 `ssh-agent` 管理。
- **网络自愈心跳**：按主机默认账号定时检查。连续异常达到阈值后自动重连并复查；
  账号或运营商不匹配时会纠正会话；外网可达但门户暂时无法识别时保持现状。
  每台主机可独立启停，并支持随 Windows 登录最小化启动。
- **本机 Windows 联网**：无需 SSH，直接检查当前电脑的认证状态，并使用所选账号和运营商连接。
  “本机 Windows”也可以绑定默认账号、启用自动自愈；如果当前账号或运营商不一致，
  程序会先结束旧会话，再按所选配置连接。

桌面配置保存在：

```text
%APPDATA%\YSUNetloginManager\config.json
```

使用前只需保证 Windows 的 `ssh` 命令可以免交互连接目标主机，且目标服务器有 `python3`。
默认情况下 EXE 会携带本目录的 `netlogin.py`，通过 SSH 临时投递到远端运行，运行后删除临时文件；
用户不需要手动把 `netlogin.py` 下载到固定目录。主机连接参数来自 `~/.ssh/config`；校园网账号、
密码、默认运营商和“某个目标默认使用哪个账号”的映射保存在桌面程序配置中。只有在“连接目标”页
显式填写远端 `netlogin.py` 绝对路径时，程序才会改用远端已有脚本。“下线当前服务器”
只会让所选 SSH 主机当前出口的校园网会话下线，不会删除本地保存的账号配置，也不是批量踢掉
某个账号名下所有设备。如果需要管理某个账号名下的在线设备，请进入“校园网账号”页，
选择账号后先“查询在线设备”，再“踢选中设备”或“踢全部设备”。该功能使用 auth1
的在线设备接口，按 `onlineUserUuid` 下线设备。如果所选账号就是本机当前在线账号，程序会
复用本机认证会话，避免 CAS 重复登录返回 HTTP 401；列表严格显示学校接口实际返回的设备，
不会把使用其他账号配置登录的设备合并进来。

#### 查询执行位置、所需条件与权限边界

查询机器当前登录的校园网账号不必与所选账号相同。账号相同时，程序会复用当前 auth1
会话；账号不同时，程序会使用所选账号保存的密码建立独立的只读 CAS 查询会话，不执行
`serviceLogin`，也不会把查询机器切换到所选账号。要可靠查询指定账号的在线设备，查询机器
需要同时满足以下条件：

1. 能访问 `auth1.ysu.edu.cn` 和学校 CAS，而不只是能够 SSH 到某台校内服务器；
2. 请求经过学校认可的校园网出口或 NAS 环境，并能获得新的 auth1 `sessionId`；
3. 拥有被查询账号的有效账号和密码，且没有被验证码、风控或学校策略阻止；
4. 学校接口允许该账号读取 `getOnlineUserInfo`、`findDevice` 和相关设备管理接口。

仅通过 WireGuard 访问 `10.20.*` 地址不一定能让本机直接获得有效的校园网认证上下文。因此，
桌面管理器可以把查询位置设置为 4090 等已接入校园网的 SSH 主机，在远端建立所选账号的
只读会话，再把结构化查询结果返回本机。

在以上条件满足且账号属于操作者或已获账号所有者授权时，可以查看该账号由学校接口返回的
在线设备，并按 `onlineUserUuid` 选择下线某一台或全部设备。SSH 权限本身不等于校园网账号
权限：没有目标账号的有效凭据，不能查询或下线其设备；学校接口若限制设备管理、触发验证码
或改变协议，操作也可能被拒绝。

同理，拥有另一台校园网机器的 SSH 登录权限后，可以让桌面管理器在该机器上使用已保存的
账号进行运营商登录，但仍须满足：该机器能够访问校园网认证入口、远端具备可用 Python、
SSH 用户有执行权限、账号密码有效，并且该账号确实开通了所选运营商服务。不能把账号未开通
的中国移动、中国联通或中国电信服务强行登录；远程登录还可能受到学校的同时在线设备数、
来源 IP、NAS、风控和账号使用政策限制。登录前应确认不会覆盖该机器现有的重要校园网会话。

重新构建 EXE：

```powershell
.\build_desktop_exe.ps1
```

安全版输出位于 `dist-secure/YSU-Netlogin-Healing-Manager.exe`。构建脚本在打包前后执行
`security_audit.py`；若发现私钥正文边界、常见私钥文件名或本机 `.ssh` 私钥路径，构建会
立即失败。PyInstaller 每次使用独立的 `build-secure/时间戳-PID/` 工作目录，避免旧构建
内容混入新产物。

完整发布包可执行：

```powershell
.\release_windows.ps1 -Version 2.1.2
```

流水线会依次运行自动测试、安全审计、PyInstaller 打包、EXE `--self-test`、发布目录审计、
ZIP 压缩和 SHA256 生成。输出位于 `release/`，不会把本机账号配置、证书、SSH 私钥或构建缓存提交到 Git。

### 单文件、单指令登录

需要 `netlogin.py` 和 Python 3。新版 auth1 认证的 AES 加密还需要 PyCryptodome、`cryptography` 或 OpenSSL 命令之一；网络助手 Windows 桌面运行时已包含 `cryptography`，可直接使用该运行时。将源码复制到目标服务器后执行：

```bash
python3 netlogin.py '校园网账号' '校园网密码' 1
```

最后一个参数是运营商：`0` 校园网、`1` 中国移动、`2` 中国联通、`3` 中国电信。
该形式最方便，但密码可能短暂出现在 shell 历史和进程参数中。自动化程序推荐通过标准输入传递 JSON：

```bash
printf '%s' '{"username":"校园网账号","password":"校园网密码","service":"1"}' |
  python3 netlogin.py login-stdin
```

`login-stdin` 的返回值是 JSON，成功退出码为 `0`，认证失败为 `1`，输入格式错误为 `2`。

### LAN Web GUI

`lan_gui.py` 是一个仅使用 Python 标准库的集中控制台。它在本机通过固定 SSH
白名单访问 4090、5080 或其他已配置的校园网服务器，并只允许调用远端
`netlogin.py login-stdin/current-status`，不提供任意命令执行入口。

准备配置：

```powershell
Copy-Item .\lan_gui_config.example.json .\lan_gui_config.json
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
.\start_lan_gui.ps1 -Password '至少10位的GUI访问密码'
```

Linux 启动：

```bash
chmod 700 start_lan_gui.sh
./start_lan_gui.sh '至少10位的GUI访问密码'
```

随后在同一 LAN 的浏览器打开 `https://GUI主机IP:8765/`。自签名证书需要由管理员
将 `lan_gui.crt` 导入客户端信任库；正式多人环境建议换成内网 CA 签发的证书。
校园网账号密码只用于当前请求，经 HTTPS 到 GUI 后再通过 SSH 标准输入传给目标服务器，
不会写入 GUI 配置、Cookie 或日志。

配置完成后，`daemon.sh` 脚本将每1分钟自动运行一次，检查网络状态并在需要时进行认证。您可以查看 `/tmp/network_check.log` 文件来检查网络状态和认证结果。

### 只读检查当前认证状态

`netlogin.py` 支持只读查询当前出口的认证状态，不会执行登录、注销、踢设备或重启网络服务：

```bash
python3 netlogin.py current-status
```

输出内容包括当前是否在线、账号、服务、IP、SSID、上线时间和 auth1 返回的在线设备列表。需要保留原始接口返回时使用：

```bash
python3 netlogin.py current-status --json
```

该命令会优先读取 auth1 新 portal 的：

- `/eportal/adaptor/getOnlineUserInfo`
- `/eportal/adaptor/devices/findDevice`
- `/eportal/operator/offlineAccountData`

如果机器未登录，会显示 `未登录/离线`，不会自动发起认证。

### 只读查询指定账号在线设备

`netlogin.py` 也支持用账号密码建立一个只读 CAS 查询会话，然后查看这个账号当前在线了几台设备：

```bash
python3 netlogin.py account-status userid password
```

在服务器管理目录中，更推荐从私有配置文件读取账号，避免密码出现在命令行和 shell 历史中：

```bash
python3 netlogin.py account-status --accounts-file ../netlogin_accounts.private.json --account-name c201-4090-mobile
python3 netlogin.py account-status --accounts-file ../netlogin_accounts.private.json --account-name c201-4090-telecom
python3 netlogin.py account-status --accounts-file ../netlogin_accounts.private.json --account-name c201-4090-mobile --json
```

这个命令只完成 CAS 登录并读取：

- `/eportal/adaptor/getOnlineUserInfo`
- `/eportal/adaptor/devices/findDevice`
- `/eportal/operator/offlineAccountData`

它不会调用 `serviceLogin`，不会注销、踢设备或重启网络服务。当前 auth1 的 `findDevice` 返回在线设备 IP、设备类型、上线时间和在线时长，但不直接返回每台设备使用的“中国移动 / 中国电信 / 校园网”服务名；只有当被查询账号正好是当前出口设备时，脚本才能从 `getOnlineUserInfo` 补齐当前设备的服务名。

### 停用 Linux systemd 自动恢复服务

部分 Linux 服务器曾经安装过旧的独立自动恢复服务：

```text
ysu-netlogin.timer
ysu-netlogin.service
```

该服务通常每 5 分钟运行 `/usr/local/sbin/ysu-netlogin-run`，并读取 `/etc/ysu-netlogin.env` 自动执行一次 `netlogin.py`。如果这份 env 里配置的是校园网账号或旧服务号，手动 `logout` 后它会很快再次自动登录，造成“服务又恢复成校园网”的现象。

如需彻底停用旧自动恢复服务，在目标 Linux 服务器上执行：

```bash
cd ~/网络登录服务器管理/ysunetlogin_openwrt
chmod +x disable_systemd_autologin.sh
sudo ./disable_systemd_autologin.sh
```

脚本会：

- 备份旧 unit、runner、`/opt/ysu-netlogin` 和 `/etc/ysu-netlogin.env` 到 `/root/ysu-netlogin-disabled-时间戳/`。
- 停止并 disable `ysu-netlogin.timer`。
- mask `ysu-netlogin.timer` 和 `ysu-netlogin.service`，防止误启动。
- 将 `/etc/ysu-netlogin.env` 改名为 `.disabled-时间戳`，防止旧服务继续读取账号配置。

脚本不会停止 `zerotier-one`，也不会停用 `netlogin-mutual-watchdog`。

## 脚本说明

### auth.sh
这个脚本负责执行实际的认证过程。它调用 `netlogin.py` 进行认证，并在失败时重启网络适配器再次尝试。

### daemon.sh
这个脚本是主要的守护进程，它负责：
- 检查当前时间是否在学校自动断网时间段内
- 定期检查网络连接状态
- 在需要时调用 `auth.sh` 进行认证
- 如果多次认证失败，将重启路由器
- 管理日志文件大小

## 故障排除

- 如果遇到认证问题，请检查您的学号、密码和运营商编号是否正确。
- 确保 `netlogin.py`、`auth.sh` 和 `daemon.sh` 文件都存在且有正确的执行权限。
- 检查 `/tmp/auth.log` 和 `/tmp/network_check.log` 文件以获取详细的错误信息。
- 如果脚本在自动断网时间段内未按预期工作，请检查 `daemon.sh` 中的 `start_hour` 和 `end_hour` 设置。

## 贡献

欢迎提交问题报告和改进建议。如果您想贡献代码，请提交 pull request。

## 许可证

本项目采用 GPL（GNU General Public License）开源许可证。您可以自由地使用、修改和分发本软件，但必须保持开源并使用相同的许可证。详细信息请参阅 [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.en.html)。

## 免责声明

本项目仅供学习和研究使用。使用本脚本可能违反校园网使用规定，使用者需自行承担风险。作者不对使用本脚本导致的任何问题负责。
