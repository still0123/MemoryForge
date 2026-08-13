#!/bin/bash
# 探测 Claude 应用的真实安装位置，结果写入同目录 claude-app-path.txt
OUT="$(cd "$(dirname "$0")" && pwd)/claude-app-path.txt"
{
  echo "=== 运行中的 Claude 进程（.app 路径）==="
  ps aux | grep -i "\.app/Contents/MacOS" | grep -iv grep | grep -i claude
  echo
  echo "=== /Applications 下的 Claude 相关应用 ==="
  ls -d /Applications/*laude*.app 2>/dev/null || echo "(无)"
  echo
  echo "=== ~/Applications 下的 Claude 相关应用 ==="
  ls -d "$HOME"/Applications/*laude*.app 2>/dev/null || echo "(无)"
  echo
  echo "=== 应用数据目录 ==="
  ls -d "$HOME"/Library/Application\ Support/Claude* 2>/dev/null || echo "(无)"
} | tee "$OUT"
echo
echo "结果已写入: $OUT"
read -rp "按回车关闭本窗口..." _
