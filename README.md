# 中转站 Image 图像生成配置器

一个独立的 Codex Skill：**先引导用户在终端配置自己的 API Key 和 API 基础 URL，验证通过后，才能通过中转站生成图片。**

- 路径：`POST {BASE_URL}/responses` + `image_generation` 工具。
- 主模型与图片模型分别配置；默认 `gpt-5.5` + `gpt-image-2`。
- Python 3.9+ 标准库实现，无需安装 OpenAI SDK 或其他 pip 包。
- 不包含作者的 API Key、私人中转地址或账单数据。
- 不依赖 CC Switch；不修改系统 imagegen skill。
- 当前版本支持文字生成单张 PNG，不包含图片编辑、多图批量或透明背景流程。

> 这是第三方中转配置工具，不是 OpenAI 官方产品。只有你信任的中转才能接收你的密钥和提示词。兼容 API 格式并不保证模型身份、账号权限、质量参数或计费与官方完全一致。

## 1. 安装

需要 Git、Python 3.9+；交互配置向导支持 macOS/Linux 的 Bash 或 Zsh。Windows 用户可使用 WSL，或在 PowerShell 中自行配置进程环境变量后运行 Python CLI。

```bash
git clone https://github.com/Xiangcainoeat/relay-image-configurator.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/relay-image-configurator"
```

目标目录已存在时不要覆盖，先确认它的来源。仓库为私有时，需要仓库访问权限和 GitHub Git 认证；也可用已经登录的 `gh repo clone`。安装后重新打开 Codex 会话，让新 skill 被发现。**安装不会配置密钥，也不会执行生图请求。**

## 2. 首次配置（必须先完成）

### 推荐：终端交互向导

在**你自己的终端**执行，不要把 API Key 发进聊天：

```bash
cd "${CODEX_HOME:-$HOME/.codex}/skills/relay-image-configurator"
source scripts/configure.sh
```

向导依次要求输入：

1. 你信任的中转站 API 基础 URL，例如 `https://relay.example.com/v1`。
2. 你的 API Key，输入时隐藏字符。

成功后，它只在当前终端导出：

```text
RELAY_IMAGE_API_KEY       必填：你的中转密钥
RELAY_IMAGE_BASE_URL      必填：HTTPS API 基础 URL
RELAY_IMAGE_DRIVER_MODEL  可选：主模型，默认 gpt-5.5
RELAY_IMAGE_MODEL         可选：图片模型，默认 gpt-image-2
```

**必须使用 `source`，不能用 `bash scripts/configure.sh` 代替。** 普通子进程无法更改父终端环境。向导不把密钥写入磁盘、不改 shell 启动文件、不访问网络。

URL 应当是 API **基础地址**，不是网站首页、管理后台或完整的 `/responses`、`/images/generations` 地址。很多中转需要 `/v1`，以运营方提供的文档为准；脚本不会猜测或补上 `/v1`。

本工具刻意不读取通用 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，避免把其他服务的密钥误发到中转。若这两个通用变量已经在同一终端正确配对，可自行明确转入：

```bash
export RELAY_IMAGE_API_KEY="$OPENAI_API_KEY"
export RELAY_IMAGE_BASE_URL="$OPENAI_BASE_URL"
```

不要在启用 `set -x` 的调试会话中执行以上手动赋值。向导会在读取和导出密钥期间关闭命令跟踪。

### 环境变量的作用范围

- 配置只影响**当前终端和它之后启动的子进程**。关闭终端后需要重新配置。
- 已经打开的 Codex 桌面窗口、IDE 或其他终端**不会自动获得这些变量**。
- 若用 Codex CLI，请在上述终端中启动 `codex`，让它继承变量；前提是你已安装该 CLI。
- 若使用桌面客户端，只在普通终端配置成功还不够：必须让客户端提供的实际执行环境也获得这两个变量，再由 skill 运行 `doctor` 验证。不要反复点击生成来测试环境继承。
- 需要永久保存时，请使用自己信任的密钥管理器或受保护的本地配置方案。本工具不自动写 `.zshrc`、`.bashrc`、`.env` 或系统登录环境，也不建议把真实密钥直接写进 shell 历史。

### PowerShell 手动配置

```powershell
$env:RELAY_IMAGE_BASE_URL = Read-Host "HTTPS API base URL"
$key = Read-Host "API Key" -AsSecureString
$env:RELAY_IMAGE_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
Remove-Variable key
python scripts/relay_image.py doctor
```

环境变量必然以进程可读取的形式存在；隐藏输入不等于加密保存。不要打印进程环境或把它放进错误报告。

## 3. 验证配置（默认不联网）

```bash
python3 scripts/relay_image.py doctor
```

缺少 Key、URL 不合法等情况会返回非零退出码。`doctor` **不会证明 Key 有效、模型可用或生图一定成功**，只进行本地环境校验。

主动检查网络和模型列表：

```bash
python3 scripts/relay_image.py doctor --list-models
```

此命令仅请求 `GET /models`，不主动调用生图；是否有请求费用以中转规则为准。列表中有模型也不等于上游账号支持它。

## 4. 生成第一张图片（可能产生费用）

```bash
python3 scripts/relay_image.py generate \
  --prompt "一只毛茸茸的小橘猫，完整坐姿，写实宠物摄影，柔和自然光，无文字或水印" \
  --out "$PWD/outputs/cat.png"
```

切换主模型，但保留同一个图片工具模型：

```bash
python3 scripts/relay_image.py generate \
  --driver-model gpt-5.6-sol \
  --image-model gpt-image-2 \
  --prompt "一只小猫坐在窗边，柔和自然光" \
  --out "$PWD/outputs/cat-sol.png"
```

也可配置默认模型：

```bash
export RELAY_IMAGE_DRIVER_MODEL="gpt-5.5"
export RELAY_IMAGE_MODEL="gpt-image-2"
```

CLI 参数 > 对应环境变量 > 内置默认值。`gpt-5.5`、`gpt-5.6-sol` 和 `gpt-6-astra` 曾在一个中转环境通过测试，但**不保证你的账号可用**。不要自动轮询模型或付费重试。

长提示词可使用 `--prompt-file /absolute/path/prompt.txt`；与 `--prompt` 二选一。仅查看请求：

```bash
python3 scripts/relay_image.py generate \
  --prompt "一只小猫" --out "$PWD/outputs/preview.png" --dry-run
```

`--dry-run` 不访问网络、不保存图片，仍要求配置环境。不要把敏感个人资料放入提示词；实际生成的侧车 JSON 会保存提示词及服务返回的改写提示词，便于复现。

## 5. 输出与费用

每次生成：

- `cat.png`：真实返回的 PNG；已有图片或同名 JSON 时拒绝请求，不覆盖。
- `cat.json`：请求的模型/质量/尺寸、返回模型名（如有）、耗时、用量、工具元数据、警告和提示词；**不包含认证头、Key 或基础 URL**。

失败时留下脱敏 JSON 诊断记录，不保留空 PNG；重新尝试需要新文件名。每次只有一个请求、**没有自动重试**；超时、断流或下载失败不代表上游没有生成或扣费。

中转可能改写参数或返回与请求不同的尺寸/质量，脚本会记录差异，不能仅凭请求模型名证明实际底层模型身份。

官方 Responses API 的主模型 token 费用和图片生成费用是不同部分；中转自身计费及其上游订阅额度不能直接套用官方价目表。脚本保留返回的 `usage`，**不据此计算完整账单或宣称扣了哪个额度池**。

## 6. 在 Codex 中调用

```text
使用 $relay-image-configurator，先检查终端环境配置，再生成一只小猫。
```

如果只是请求“帮我配置”，skill 应停在引导/检查阶段；付费生成测试图需要你的明确要求。

## 安全与实现边界

- 仅 HTTPS，保持系统证书验证；拒绝 URL 内凭据、查询参数、片段和重定向。
- 不从钥匙串、其他 skill、CC Switch、浏览器会话或作者配置中读取凭据。
- 为避免隐式凭据路由，不读取 `HTTP_PROXY` / `HTTPS_PROXY` / 系统代理；当前版本需要可直接访问的 HTTPS 中转。
- 不安装依赖，不修改全局 shell 配置，不扫描用户目录。
- 流式响应读取有大小和超时限制；`--max-seconds` 在读取间检查，单次阻塞读取仍受 `--timeout` 限制，不是严格的总时长硬截止。
- 错误会替换当前 Key 和 Bearer token，但提交诊断前仍应人工检查其中的提示词和服务返回内容。

故障处理见 [references/troubleshooting.md](references/troubleshooting.md)。

## 本地测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用伪造响应，不发送网络请求、不需要真实密钥，也不会生成费用。

## 官方参考

- [Responses 图片生成工具](https://developers.openai.com/api/docs/guides/tools-image-generation)
- [Image generation 与费用说明](https://developers.openai.com/api/docs/guides/image-generation)
- [GPT-5.5 模型能力](https://developers.openai.com/api/docs/models/gpt-5.5)

这些链接解释官方 API，不承诺任何第三方中转的实现或账单规则。
