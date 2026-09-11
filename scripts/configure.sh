#!/usr/bin/env bash
# Source this file from Bash or Zsh. No credentials are written to disk.
if [ -n "${BASH_VERSION:-}" ]; then
  if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    printf '%s\n' '请使用 source scripts/configure.sh；直接执行无法配置父终端环境。' >&2
    exit 1
  fi
elif [ -n "${ZSH_VERSION:-}" ]; then
  case "$ZSH_EVAL_CONTEXT" in
    *:file*) ;;
    *) printf '%s\n' '请使用 source scripts/configure.sh。' >&2; exit 1 ;;
  esac
else
  printf '%s\n' '此向导支持 Bash / Zsh；其他 shell 请按 README 手动配置。' >&2
  return 1 2>/dev/null || exit 1
fi

_relay_image_configure() {
  local _relay_trace=0
  case $- in *x*) set +x; _relay_trace=1 ;; esac
  local _relay_file _relay_dir _relay_url _relay_key _relay_status=0
  if [ -n "${BASH_VERSION:-}" ]; then
    _relay_file="${BASH_SOURCE[0]}"
  else
    _relay_file="${(%):-%x}"
  fi
  _relay_dir="$(cd -- "$(dirname -- "$_relay_file")" && pwd)" || return 1
  if [ ! -t 0 ]; then
    printf '%s\n' '请在交互式终端中运行向导；不要在聊天中提供密钥。' >&2
    return 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '需要 Python 3.9+。请先自行安装 Python，再运行向导。' >&2
    return 1
  fi
  printf '%s\n' '中转站 Image 图像生成配置器' '仅配置当前终端环境，不写文件、不发网络请求。' 'API 基础 URL 示例：https://relay.example.com/v1'
  printf '请输入你信任的中转站 API 基础 URL：'
  IFS= read -r _relay_url || return 1
  printf '请输入 API Key（输入不显示）：'
  IFS= read -r -s _relay_key || { printf '\n' >&2; return 1; }
  printf '\n'
  RELAY_IMAGE_BASE_URL="$_relay_url" RELAY_IMAGE_API_KEY="$_relay_key" \
    python3 "$_relay_dir/relay_image.py" doctor || _relay_status=$?
  if [ "$_relay_status" -eq 0 ]; then
    export RELAY_IMAGE_BASE_URL="$_relay_url"
    export RELAY_IMAGE_API_KEY="$_relay_key"
    export RELAY_IMAGE_DRIVER_MODEL="${RELAY_IMAGE_DRIVER_MODEL:-gpt-5.5}"
    export RELAY_IMAGE_MODEL="${RELAY_IMAGE_MODEL:-gpt-image-2}"
    printf '%s\n' '配置完成。当前终端及它随后启动的子进程可以使用；已运行的 GUI 不会自动获得新变量。' '下一步：python3 scripts/relay_image.py doctor' '生图会产生费用；doctor 默认仅本地检查。'
  fi
  # Clear local secrets before restoring a caller's tracing setting.
  unset _relay_key _relay_url
  if [ "$_relay_trace" -eq 1 ]; then set -x; fi
  return "$_relay_status"
}
_relay_image_configure
