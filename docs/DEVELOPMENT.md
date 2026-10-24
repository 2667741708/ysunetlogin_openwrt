# 开发与打包

所有命令从仓库根目录运行。普通用户直接下载 Releases 的 netlogin.pyz，无需执行本页命令。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

桌面相关测试需要 Python 附带的 tkinter；Linux 可使用发行版 python3-tk。网络认证测试使用模拟响应，不会主动修改真实校园网会话。

## 构建单文件命令包

```bash
python scripts/build_cli_bundle.py
python release/netlogin.pyz --version
python release/netlogin.pyz --help
```

输出 release/netlogin.pyz，包含全部命令运行模块和 Windows 网卡检测资源，不包含账号或 Python 本身。构建程序同时输出 SHA-256。发布时应上传该文件与 SHA256SUMS.txt，而不是要求用户手工复制多个源码模块。

## Windows 桌面构建

```powershell
.\scripts\build_desktop_exe.ps1
.\scripts\release_windows.ps1 -Version 2.2.0
```

需要 PyInstaller、认证加密依赖与脚本指定位置的 TigerVNC Viewer。发布流程包含测试、凭据审计和 EXE 自检；相对路径以仓库根目录为准。

## 文件维护

业务模块保留在根目录，避免改变已有源码入口与临时 SSH 运行方式。构建工具在 scripts，配置模板在 examples，旧环境迁移工具在 legacy；旧实验记录集中在 docs/experiments。确认无用途的备份文件不进入版本控制。
