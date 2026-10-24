# 旧版本迁移工具

新用户不需要这里的文件。当前入口是 Releases 的 netlogin.pyz，或根目录 netlogin.py。

- auth.sh：旧路由器登录入口，仅为已有部署保留。
- daemon.sh：旧 cron 入口，现在只查询心跳状态，不再重连或重启设备。
- local_netlogin_guard.py：旧账号文件配合 timer 的检查器。不要与新心跳同时运行。
- disable_systemd_autologin.sh：停用旧 ysu-netlogin.timer/service，需管理员权限；会备份相关文件。仅用于确认存在旧服务的机器，不作为安装步骤。

新版本登录会自动启动心跳，logout 同时停止。已有部署升级时，应确认没有其他旧 timer/cron 在争用账号；本次目录整理没有自动修改任何服务器上的服务。
