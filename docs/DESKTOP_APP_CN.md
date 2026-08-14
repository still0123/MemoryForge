# 桌面端（macOS / Windows）

MemoryForge 桌面端把现有本地知识门户放进原生窗口。它不打开浏览器，也不访问远程网页；
窗口关闭后，本机门户服务自动停止。

## 开发运行

macOS：

```console
python -m pip install -e '.[desktop]'
memoryforge desktop --workspace /path/to/workspace
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\memoryforge.exe desktop --workspace C:\path\to\workspace
```

不传 `--workspace` 时，应用会重开上次使用的 Workspace；没有历史记录时会显示系统文件夹
选择器。请选择已经执行过 `memoryforge init` 的 Workspace，而不是 MemoryForge 的源码目录。
可用 `--choose-workspace` 强制重新选择。

## 构建可双击的应用

### macOS

```bash
python -m pip install -e '.[desktop]'
./scripts/build_macos_app.sh
open dist/MemoryForge.app
```

产物是 `dist/MemoryForge.app`。第一次分发给其他 Mac 前，仍需用你的 Apple Developer 身份签名和公证；本地构建供本机使用无需该步骤。

如果构建 Python 不在当前 shell 的 `PATH`，可改用 `PYTHON=/path/to/python ./scripts/build_macos_app.sh`。

### Windows

PyInstaller 不支持跨系统构建，请在 Windows 10/11 主机上运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_app.ps1
.\dist\MemoryForge.exe
```

产物是单文件 `dist\MemoryForge.exe`。脚本默认使用 `.venv\Scripts\python.exe`；也可通过
`-Python C:\path\to\python.exe` 指定解释器。Windows 需要 Microsoft Edge WebView2 Runtime，
Windows 10/11 通常已预装。

## 边界

- PyInstaller 必须在目标系统上分别构建 `.app` 和 `.exe`。
- Portal 继续仅绑定 `127.0.0.1` 的随机端口，端口不展示给用户。
- 仍可用原有 `memoryforge start` 在浏览器中打开 Portal。
- 当前 Windows 桌面壳、Workspace 打开和查询入口已有实现；依赖 POSIX `dir_fd`、
  `O_DIRECTORY`、`O_NOFOLLOW` 的安全导入和 ChangeSet 写路径仍需原生 Windows 门禁，
  因此本次不宣称完整 Windows 发布支持。
