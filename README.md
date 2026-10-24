# 燕山大学校园网自动登录与重连

## 1. 准备环境

安装 Python 3（建议 3.10+），并接入燕山大学校园网。Linux 可将下文的 `python` 换成 `python3`。

Windows 可使用系统加密接口；Linux 需有 OpenSSL，或安装 `cryptography`。

## 2. 下载一个文件

下载 [netlogin.pyz](https://github.com/2667741708/ysunetlogin_openwrt/releases/latest/download/netlogin.pyz)，无需解压。

`.pyz` 内置项目模块，但不包含 Python。源码 `netlogin.py` 依赖其他文件，不可单独下载。无需安装 OpenWrt。

## 3. 登录

在文件所在目录打开终端，运行：

```bash
python netlogin.pyz login
```

按提示输入学号、密码和服务编号，密码不回显：

**0 校园网｜1 移动｜2 联通｜3 电信**，请选择账号已开通的服务。

看到“已自动启动后台自愈心跳”即完成。默认每 60 秒检查，连续 2 次异常后重连并验证；持续失败会延长重试间隔。

关闭终端或退出 SSH 后仍会维护连接。机器重启后，需再次登录。学校停网、账号欠费或上游故障时，自动重连不能保证恢复网络。

## 4. 查询与下线

```bash
python netlogin.pyz current-status
python netlogin.pyz heartbeat-status
python netlogin.pyz logout
```

依次用于查询校园网状态、查看心跳状态、停止心跳并下线。主动下线后不会被心跳重新登录。

只想停止维护、保留连接，执行 `python netlogin.pyz heartbeat-stop`。

## 5. 遇到问题

找不到文件时切换到下载目录；认证失败时核对账号、服务及校园网直连。

[安装与依赖](docs/CLI_INSTALL.md) · [全部命令](docs/命令大全.md) · [排错](docs/CAMPUS_DIRECT_SETUP.md) · [开发](docs/DEVELOPMENT.md)
