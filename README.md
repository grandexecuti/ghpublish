# ghpublish

一条命令把本地项目整理并开源到 GitHub：先体检密钥和大文件，再按代码内容写 README，最后建仓库、推送、设置简介和 topics。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)

> **ghpublish** — audit a local project, generate the open-source boilerplate, create the GitHub repo and push, in one command.

## 为什么

把一个项目开源出去，机械步骤很多：补 README、选许可证、写 `.gitignore`、确认没把 `.env` 和密钥推上去、建仓库、推代码、设置简介和 topics。漏掉任何一步都可能很尴尬，尤其是密钥泄露——公开仓库会被爬虫和镜像站立刻收录。

这个工具把机械部分自动化，README 的实质内容交给 AI 读代码来写。默认私有、默认只打印计划，公开必须显式确认。

## 功能

- **体检**：识别 GitHub / OpenAI / AWS / Google / Slack / Stripe token、Telegram bot token、私钥块、`.env`、`id_rsa`、cookie 文件、高熵字符串；检查超大文件和垃圾二进制
- **生成**：按语言生成 `.gitignore`，生成 MIT LICENSE 和 README 骨架，初始化 git 并首次提交
- **发布**：创建仓库、推送、设置简介和 topics，输出仓库 URL
- **安全默认**：默认私有；`publish` 不加 `--confirm` 只打印计划；已有 README/LICENSE 一律不覆盖
- **批量挑选**：`list` 扫描目录，按项目标记或源码数量找出候选项目
- **AI 驱动**：配套 ZCode skill，说一句「把这个项目开源」即可走完全流程

## 安装

Windows（不需要管理员权限，全部装进用户目录）：

```powershell
git clone https://github.com/grandexecuti/ghpublish.git
cd ghpublish
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会下载便携版 Git 和 GitHub CLI 到 `%USERPROFILE%\tools`，把 `ghpublish` 加入用户 PATH。删掉 `tools` 目录即可完全卸载。

其他平台只要系统里有 `git` 和 `gh`，直接 `python ghpublish.py ...` 也能用。

## 首次登录

```powershell
ghpublish doctor
gh auth login -h github.com -p https -w
```

登录只需一次，凭据保存在 `%APPDATA%\GitHub CLI`。

## 用法

```powershell
# 0. 列出目录下像项目的子目录
ghpublish list D:\code

# 1. 体检: 密钥 / 超大文件 / 垃圾文件 / 语言构成
ghpublish scan D:\code\my-project

# 2. 生成 .gitignore / LICENSE / README, 初始化 git 并首次提交
ghpublish init D:\code\my-project --name my-project --desc "一句话简介"

# 3. 预览发布计划 (不执行)
ghpublish publish D:\code\my-project --name my-project --desc "一句话简介" --topics cli,python

# 4. 发布 (默认私有)
ghpublish publish D:\code\my-project --name my-project --confirm

# 5. 公开开源
ghpublish publish D:\code\my-project --name my-project --public --confirm
```

`scan --json` 输出机器可读结果，方便接进 CI 或 AI 流程。

## AI 驱动

仓库里的 [`skills/github-publish/SKILL.md`](skills/github-publish/SKILL.md) 是一份 ZCode skill。把它放到 `~/.agents/skills/github-publish/`，之后对 AI 说「把这个项目开源」，它会：

1. 跑 `doctor` 检查登录
2. 跑 `scan` 处理密钥和大文件
3. 读入口文件、依赖清单和目录结构，写一份有真实内容的 README（不是占位符）
4. 跑 `init` 生成开源文件
5. 先 dry-run 给你看计划，确认后加 `--confirm` 发布

批量场景下逐个项目串行走同一套流程，任何一个有严重密钥问题都会单独停下。

## 安全设计

- **默认私有**：只有显式 `--public --confirm` 才会创建公开仓库，并提示公开不可撤回
- **密钥拦截**：命中严重级别时以退出码 2 中止，输出只显示打码后的片段
- **不覆盖**：`init` 不碰已存在的 README / LICENSE / `.gitignore`
- **可预览**：`publish` 默认 dry-run
- **隐私邮箱**：没有全局 git 身份时使用 `<用户名>@users.noreply.github.com`，不暴露真实邮箱
- **不改历史**：工具不会 `push --force`，也不会改写已有提交

## 配置

`%USERPROFILE%\.ghpublish\config.json`：

```json
{
  "author": "Your Name",
  "email": "you@users.noreply.github.com",
  "license": "MIT",
  "git": "C:\\Users\\you\\tools\\git\\cmd\\git.exe",
  "gh": "C:\\Users\\you\\tools\\gh\\bin\\gh.exe"
}
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `doctor` | 检查 git / gh / 登录状态 |
| `list <dir>` | 列出目录下的候选项目 |
| `scan <path>` | 发布前体检，`--json` 输出机器可读结果 |
| `init <path>` | 生成开源文件 + `git init` + 首次提交 |
| `publish <path>` | 建仓库并推送，默认私有、默认 dry-run |
| `status <path>` | 分支 / 远端 / 提交数 / 工作区状态 |

## 限制

- 安装脚本只覆盖 Windows；工具本体跨平台
- 不做定时无人值守发布：公开不可撤回，自动扫描任意目录风险太高
- 不处理 Git LFS，超过 100MB 的文件会被直接拦下
- 扫描有上限（8000 文件 / 200MB），超大仓库请分目录体检

## License

[MIT](LICENSE)
