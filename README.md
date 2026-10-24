# 燕山大学校园网自动登录与自愈心跳

登录成功后自动维持校园网连接：每 60 秒检查，连续 2 次异常后重连并验证。无需设置重连时间段或定时任务。

## 最简单的使用方式

**普通用户只下载 [netlogin.pyz](https://github.com/2667741708/ysunetlogin_openwrt/releases/latest/download/netlogin.pyz)，不用下载源码。**

它是把认证、心跳和网络检测模块装在一起的单文件 Python 程序，不需要解压。需要本机有 Python 3（建议 3.10 或更新），并已接入燕山大学校园网。

```bash
python netlogin.pyz login
```

按提示输入学号、密码和服务编号。密码不会显示，也不会写进这条终端命令。Linux 如找不到 python，请用 python3。

服务编号：**0 校园网 / 1 中国移动 / 2 中国联通 / 3 中国电信**。只能使用账号已开通的服务。

看到“已自动启动后台自愈心跳”即完成。关闭终端或退出 SSH 后，命令行心跳仍继续运行；机器重启后，再登录一次即可重新启动。重连持续失败时逐步延长等待，最长 15 分钟。

| 操作 | 命令 |
| --- | --- |
| 查看当前校园网状态 | `python netlogin.pyz current-status` |
| 查看自动维护状态 | `python netlogin.pyz heartbeat-status` |
| 下线并停止自动维护 | `python netlogin.pyz logout` |
| 只停止维护，保留连接 | `python netlogin.pyz heartbeat-stop` |

Windows 自带的加密接口可用于认证；Linux 需有 OpenSSL 或 Python 加密库之一。依赖缺失与校验方法见 [安装说明](docs/CLI_INSTALL.md)。单文件包不包含 Python 本身。

## 能否只下载 netlogin.py？

**不能只下载仓库根目录的 netlogin.py 来使用完整的自动重连功能。** 它是源码入口，会调用同目录的心跳与网络检测模块。

- **只想使用**：下载上面的 `netlogin.pyz`，已内置所需项目模块。
- **想修改源码**：克隆仓库或下载完整源码 ZIP，然后在仓库目录执行 `python netlogin.py login`。
- **需要 Windows 窗口界面**：见 [桌面版说明](docs/WINDOWS_LOCAL_SETUP.md)。旧 2.1.4 EXE 不包含新的自动启用行为；本次单文件发布是命令行包。

仓库名称中的 OpenWrt 指一种路由器系统，只是可选运行环境。使用电脑或服务器不需要 OpenWrt。

## 更多用法

- [安装、完整源码与依赖](docs/CLI_INSTALL.md)
- [全部命令](docs/命令大全.md)
- [校园网直连、网卡与代理排错](docs/CAMPUS_DIRECT_SETUP.md)
- [Windows 桌面程序](docs/WINDOWS_LOCAL_SETUP.md) · [LAN 网页控制台](docs/LAN_GUI.md)
- [认证流程](docs/AUTH_FLOW.md) · [更新记录](docs/RELEASE_NOTES.md)
- [开发、测试和打包](docs/DEVELOPMENT.md) · [旧版本迁移](legacy/README.md)

## 仓库结构

```text
netlogin.py          源码登录入口
*_heartbeat.py       自动维护连接
campus_*/wifi_*      校园网与网卡检测
self_service.py      账号、设备和流量查询
desktop_gui.py       Windows 桌面入口
lan_gui.py           LAN 网页入口
docs/               使用文档、更新记录和实验资料
scripts/            开发者构建与启动工具
examples/           可选配置示例
legacy/             旧部署兼容与迁移工具（新用户无需使用）
tests/              自动测试
```

普通使用无需操作 scripts、examples、legacy 或 tests。运行时保存的账号配置不会进入发布包；Linux 使用当前用户私有目录，Windows 使用当前用户 DPAPI 加密。网络状态输出不含重连密码。
