---
name: relay-image-configurator
description: Configure a user's trusted OpenAI-compatible image relay in terminal environment variables, validate the setup, and generate PNG images through its Responses image_generation tool. Use when the user asks to set up a relay API key/URL, configure relay image generation, or generate an image through this configured relay. Not for unrelated model routing, vector assets, or editing existing images.
---

# 中转站 Image 图像生成配置器

帮助用户先在终端配置自己的 API Key 和 URL，然后通过中转站 `/responses` 调用图片生成工具。独立运行，不依赖系统 imagegen skill、CC Switch 或作者的账号。

## 先配置，后使用

1. 将此 skill 的实际目录记作 `SKILL_DIR`。先运行：
   ```bash
   python3 "$SKILL_DIR/scripts/relay_image.py" doctor
   ```
   此命令默认只检查环境，不访问网络、不生成图片，也不显示密钥。
2. 缺少 `RELAY_IMAGE_API_KEY` / `RELAY_IMAGE_BASE_URL` 或校验失败时，**停止生图**，引导用户在自己的交互式 Bash/Zsh 终端运行：
   ```bash
   cd "$SKILL_DIR"
   source scripts/configure.sh
   ```
   详见 [README 的首次配置](README.md#2-首次配置必须先完成)。不要索要聊天中的密钥，不读取其他工具的凭据，不猜测中转地址，不自动设置持久环境。
3. 用户完成配置后，在实际执行生图的进程环境中再次运行 `doctor`。终端变量不会自动进入已启动的 Codex GUI；只在用户终端验证通过，不代表 agent 执行环境也已配置。缺变量时继续停止并解释进程继承问题，不能偷偷回退到其他密钥。
4. 用户明确要求验证网络时，可运行 `doctor --list-models`；模型出现在列表里不等于账号能实际调用。配置请求本身不授权付费生图，先询问是否需要生成测试图。

## 生图

用户已经要求生成图片，且当前执行环境校验通过后，使用本 skill 自带脚本。保留用户的主体、风格和模型选择；不要为排错自动改提示词或换模型。

```bash
python3 "$SKILL_DIR/scripts/relay_image.py" generate \
  --prompt "一只毛茸茸的小橘猫，完整坐姿，写实宠物摄影，柔和自然光，无文字或水印" \
  --driver-model gpt-5.5 \
  --image-model gpt-image-2 \
  --out /absolute/project/path/outputs/cat.png
```

- 顶层主模型负责理解请求并调用工具；图片模型负责生图。默认值分别为 `gpt-5.5` / `gpt-image-2`，也可用环境变量或参数覆盖。默认值不是对任意中转账号的可用性保证。
- CLI 参数优先于环境变量，再使用默认值。未指定模型时，命令中省略对应参数，以保留用户的环境配置。
- 这是 `/responses` 路径，不是修复 `/images/generations`。不修改系统 imagegen、其他 skill 或 CC Switch。
- 每次一张 PNG、无自动重试。一次失败就停止，报告脱敏错误；超时不代表上游未生成或未扣费，再试或换模型须用户确认。
- 输出放到当前项目/用户指定位置，用绝对路径和新文件名，不覆盖旧图。`--dry-run` 只显示脱敏请求，不访问网络。
- 用可用的图片查看工具检查输出，再向用户展示图片、绝对路径、最终提示词、请求模型及耗时。核对 JSON 元数据的 `actual_size`、工具返回的质量及 `warnings`；不要把请求值当作实际输出值。
- 官方 API 主模型 token 费用与图片费用分开计算；私有中转及其上游账号怎样扣额度要看实际账单。返回 `usage` 不一定包含完整图片费用，不据此承诺成本。

## 问题定位

按 [references/troubleshooting.md](references/troubleshooting.md) 区分环境、权限、模型支持、工具桥接、输出参数和计费问题。禁止为“修复”而关闭 TLS 验证、自动跟随重定向、反复付费重试或修改用户中转配置。
