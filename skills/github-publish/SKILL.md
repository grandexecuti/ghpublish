---
name: github-publish
description: 把用户自己的本地项目整理并发布/开源到 GitHub：体检密钥与大文件、按代码内容撰写 README、生成 .gitignore 与 LICENSE、创建仓库并推送、设置简介和 topics。当用户说「发布到 GitHub / 开源这个项目 / 上传到 GitHub / 帮我建个仓库 / push 到远程」时使用。
---

# github-publish

目标：用户说一句「把这个项目开源」，你负责从体检到发布完成。只让用户做两件事：GitHub 登录（一次性）和确认公开。

## 工具

`ghpublish` 已安装。PATH 未刷新时用完整命令：

```powershell
python "C:\Users\ASUS\Desktop\gz\gh-publish\ghpublish.py" <cmd>
```

## 流程

### 0. 先判断能不能发

只发布用户本人原创的代码。以下内容一律不发布，直接说明原因并停止：

- 第三方软件的破解、去授权、绕过付费、外挂、木马/后门/远控
- 泄露的凭据、Cookie、数据库、抓包得到的私有协议数据
- 他人的代码、论文、数据集、未授权素材
- 含真实 `.env` / 私钥 / token 的目录（先清理再说）

### 1. 登录检查

```powershell
ghpublish doctor
```

未登录 → 告诉用户运行 `gh auth login -h github.com -p https -w`，然后停下等用户。不要代登录，不要索要密码或 token。

### 2. 体检

目标不明确或有多个项目时，先列候选：

```powershell
ghpublish list "<parent-dir>"
```

```powershell
ghpublish scan "<path>" --json
```

- 退出码 2 或 `critical_count > 0`：逐个处理。密钥移到 `.env` 并加进 `.gitignore`；无法判断的列给用户。
- 大文件 >100MB：排除或改用 Git LFS。
- 垃圾文件（exe/zip/log/db/缓存）：加 `.gitignore`。

处理完重跑 scan，直到 0 critical 再继续。

### 3. 写 README（核心步骤，不要留占位符）

先读项目：入口文件、依赖清单（package.json / pyproject.toml / requirements.txt / Cargo.toml / go.mod）、目录结构、测试。然后写 `README.md`：

- 一句话定位 + 解决什么问题
- 特性列表 3-6 条，必须能从代码里看出来，不要编
- 安装步骤、最小可运行示例（真实命令）
- 配置项 / 环境变量表
- 项目结构
- 许可证

已有 README 优先保留原内容并改进，不要整篇重写掉作者的话。

### 4. 生成开源文件

```powershell
ghpublish init "<path>" --name <repo> --desc "<一句话>" --license MIT
```

已存在的 README/LICENSE/.gitignore 不会被覆盖。作者身份可在 `%USERPROFILE%\.ghpublish\config.json` 里配置 `author` / `email`，否则使用系统的 `<用户名>@users.noreply.github.com`。

### 5. 发布

先 dry-run 给用户看计划：

```powershell
ghpublish publish "<path>" --name <repo> --desc "..." --topics a,b,c
```

计划无误、用户明确说要开源 → 加 `--public --confirm`；用户没明说公开就默认私有（只加 `--confirm`）。

公开前必须再确认一次，并说明：公开仓库会被搜索引擎、镜像站收录，内容无法真正撤回。

### 6. 汇报

给出：仓库 URL、可见性、提交数、topics、README 摘要。提醒后续更新只要 `git push`。

## 批量发布

用户给多个项目 → 逐个走上面的流程：先全部 scan，任何一个有 critical 就单独处理，全部通过后再统一发布。

## 禁止

- 不 `git push --force`，不改写已有历史
- 不往公开仓库推 `.env`、密钥、cookie、数据库、用户数据
- 不发布不属于用户的代码
- 未确认时绝不创建公开仓库
- `publish` 不加 `--confirm` 永远只打印计划，这是预期行为，不要为了「自动化」绕过它
