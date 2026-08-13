#!/bin/bash
# ============================================================
#  一键安装 Claude Desktop 简体中文补丁（修正版）
#  自动定位 Claude 应用、先预检后安装、3P/Cowork 兼容模式
#  来源：javaht/claude-desktop-zh-cn
# ============================================================
set -euo pipefail

WORK="$HOME/.claude-zh-installer"

echo "=============================================="
echo "  Claude Desktop 简体中文补丁（一键）"
echo "=============================================="
echo

# 1. 定位 Claude 应用（优先用户目录，其次系统目录）
APP=""
for c in "$HOME/Applications/Claude.app" "/Applications/Claude.app"; do
  if [ -d "$c" ]; then APP="$c"; break; fi
done
if [ -z "$APP" ]; then
  APP="$(ls -d "$HOME"/Applications/*laude*.app /Applications/*laude*.app 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$APP" ] || [ ! -d "$APP" ]; then
  echo "没找到 Claude 应用。请手动指定，例如："
  echo "  bash \"$0\" /Applications/Claude.app"
  exit 1
fi
echo "检测到 Claude 应用: $APP"
echo

# 2. 下载最新版补丁
mkdir -p "$WORK"
echo "正在下载最新版汉化补丁…"
curl -fsSL "https://codeload.github.com/javaht/claude-desktop-zh-cn/zip/refs/heads/main" -o "$WORK/repo.zip"
rm -rf "$WORK/repo"
unzip -q "$WORK/repo.zip" -d "$WORK/repo"
REPO_DIR="$(find "$WORK/repo" -maxdepth 1 -type d -name 'claude-desktop-zh-cn-*' | head -n 1)"
if [ -z "$REPO_DIR" ]; then
  echo "下载解压失败，请检查网络后重试。"
  exit 1
fi
PATCHER="$REPO_DIR/scripts/patch_claude_zh_cn.py"
PY="/usr/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
echo "补丁就绪。"
echo

# 3. 预检（dry-run：完整走一遍，但不改动你的应用、不退出 Claude）
echo "=== 第 1 步：预检（不会改动应用，稍等几十秒）==="
"$PY" "$PATCHER" --app "$APP" --user-home "$HOME" --lang zh-CN --skip-asar-patch --dry-run
echo "=== 预检通过 ==="
echo

# 4. 正式安装（会退出并重启 Claude）
echo "=== 第 2 步：正式安装 ==="
echo "⚠️  过程中会退出并重启 Claude，当前对话窗口也会被关闭。"
read -rp "按回车继续，Ctrl+C 取消: " _unused
echo

case "$APP" in
  /Applications/*)
    echo "应用在系统目录，需要 sudo（会提示输入 Mac 登录密码）。"
    sudo "$PY" "$PATCHER" --app "$APP" --user-home "$HOME" --lang zh-CN --skip-asar-patch --launch
    ;;
  *)
    "$PY" "$PATCHER" --app "$APP" --user-home "$HOME" --lang zh-CN --skip-asar-patch --launch
    ;;
esac

echo
echo "完成。若界面没变中文：左下角账号菜单 → Language → 中文（简体）。"
read -rp "按回车关闭本窗口..." _
