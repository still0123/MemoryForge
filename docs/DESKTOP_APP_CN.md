# macOS 桌面端

MemoryForge 桌面端把现有本地知识门户放进 macOS 原生窗口。它不打开浏览器，也不访问远程网页；窗口关闭后，本机门户服务自动停止。

## 开发运行

```bash
python -m pip install -e '.[desktop]'
memoryforge desktop --workspace /path/to/workspace
```

不传 `--workspace` 时，应用会重开上次使用的 Workspace；没有历史记录时会显示 macOS 文件夹选择器。请选择已经执行过 `memoryforge init` 的 Workspace，而不是 MemoryForge 的源码目录。可用 `--choose-workspace` 强制重新选择。

## 构建可双击的应用

```bash
python -m pip install -e '.[desktop]'
./scripts/build_macos_app.sh
open dist/MemoryForge.app
```

产物是 `dist/MemoryForge.app`。第一次分发给其他 Mac 前，仍需用你的 Apple Developer 身份签名和公证；本地构建供本机使用无需该步骤。

如果构建 Python 不在当前 shell 的 `PATH`，可改用 `PYTHON=/path/to/python ./scripts/build_macos_app.sh`。

## 边界

- 第一版目标为 macOS；PyInstaller 需要在目标系统上分别构建各平台产物。
- Portal 继续仅绑定 `127.0.0.1` 的随机端口，端口不展示给用户。
- 仍可用原有 `memoryforge start` 在浏览器中打开 Portal。
