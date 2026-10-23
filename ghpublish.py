#!/usr/bin/env python3
"""ghpublish - 把本地项目一键开源到 GitHub。

命令:
  doctor    检查 git / gh / 登录状态
  scan      发布前体检: 密钥泄露、超大文件、垃圾文件、语言构成
  init      生成 .gitignore / LICENSE / README 骨架, git init + 首次提交
  publish   创建 GitHub 仓库并推送 (默认私有, 必须 --confirm)
  status    查看本地仓库和远端状态

设计原则:
  - 默认不公开; 公开必须显式 --public --confirm
  - 发现密钥/私钥/凭据时拒绝发布, 除非 --allow-secrets
  - 不做任何破坏性操作; 已有文件不覆盖
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from getpass import getuser
from pathlib import Path

CONFIG_DIR = Path.home() / ".ghpublish"
CONFIG_FILE = CONFIG_DIR / "config.json"

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea",
    ".vscode", "site-packages", ".mypy_cache", ".pytest_cache", ".gradle",
    "bin", "obj", ".cache", "logs", ".zcode", ".tox", "coverage", ".terraform",
}

EXT_LANG = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".sh": "Shell", ".ps1": "PowerShell", ".lua": "Lua", ".dart": "Dart",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
    ".md": "Markdown", ".ipynb": "Jupyter", ".sql": "SQL",
}

LANG_GITIGNORE = {
    "Python": """__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ipynb_checkpoints/
""",
    "JavaScript": """node_modules/
npm-debug.log*
yarn-error.log*
.pnpm-debug.log*
coverage/
.next/
.nuxt/
dist/
""",
    "TypeScript": """node_modules/
dist/
coverage/
*.tsbuildinfo
""",
    "Go": """*.exe
*.test
*.out
vendor/
""",
    "Rust": """target/
""",
    "Java": """target/
*.class
*.jar
.gradle/
""",
    "Kotlin": """build/
.gradle/
*.class
""",
    "C": """*.o
*.obj
*.exe
*.dll
*.so
*.dylib
build/
""",
    "C++": """*.o
*.obj
*.exe
*.dll
*.so
*.dylib
build/
""",
    "C#": """bin/
obj/
*.user
""",
    "Ruby": """*.gem
.bundle/
vendor/bundle/
""",
    "PHP": """vendor/
composer.lock
""",
    "Swift": """.build/
*.xcuserstate
""",
    "Dart": """.dart_tool/
build/
""",
}

SECRET_GITIGNORE = """
# ---- secrets (ghpublish) ----
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
id_rsa*
id_ed25519*
*.kdbx
cookies*.txt
*.session
*.ovpn
"""

GENERIC_GITIGNORE = """
# ---- OS / editor ----
.DS_Store
Thumbs.db
desktop.ini
*.swp
*~
.vscode/
.idea/
"""

# (名称, 正则, 严重级别)  critical=阻止发布
SECRET_PATTERNS = [
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "critical"),
    ("GitHub Fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"), "critical"),
    ("OpenAI/Anthropic Key", re.compile(r"\b(sk-[A-Za-z0-9_\-]{24,}|sk-ant-[A-Za-z0-9_\-]{24,})\b"), "critical"),
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "critical"),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "critical"),
    ("Slack Token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"), "critical"),
    ("Stripe Live Key", re.compile(r"\bsk_live_[0-9A-Za-z]{20,}\b"), "critical"),
    ("Telegram Bot Token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"), "critical"),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "critical"),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "warning"),
    ("密码/密钥赋值", re.compile(
        r"""(?i)\b(api[_-]?key|apikey|access[_-]?token|secret[_-]?key|secret|password|passwd|pwd)\b\s*[:=]\s*["'][^"'\s]{6,}["']"""
    ), "warning"),
]

SENSITIVE_NAMES = [
    (".env", "critical"), (".env.local", "critical"), (".env.production", "critical"),
    ("id_rsa", "critical"), ("id_ed25519", "critical"),
    ("credentials", "critical"), ("hosts.yml", "warning"),
    (".npmrc", "warning"), (".pypirc", "critical"),
]
SENSITIVE_EXTS = {
    ".pem": "critical", ".key": "critical", ".p12": "critical", ".pfx": "critical",
    ".kdbx": "critical", ".ovpn": "warning", ".ppk": "critical",
    ".sqlite": "warning", ".db": "warning", ".sql": "warning",
    ".log": "warning", ".session": "warning", ".har": "warning",
}

JUNK_EXTS = {".exe", ".dll", ".so", ".dylib", ".bin", ".zip", ".7z", ".rar",
             ".iso", ".img", ".tmp", ".bak", ".log", ".pyc", ".class", ".o", ".obj"}

MAX_FILES = 8000
MAX_SCAN_BYTES = 200 * 1024 * 1024
MAX_TEXT_BYTES = 3 * 1024 * 1024
LARGE_FILE_BYTES = 40 * 1024 * 1024


# ---------------------------------------------------------------- helpers

def out(msg: str = "") -> None:
    print(msg)


def die(msg: str, code: int = 1) -> None:
    sys.stdout.flush()
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_tool(name: str, env_key: str, candidates: list[Path]) -> str | None:
    cfg = load_config()
    for cand in (os.environ.get(env_key), cfg.get(name)):
        if cand and Path(cand).exists():
            return cand
    found = shutil.which(name)
    if found:
        return found
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return None


def git_path() -> str | None:
    home = Path.home()
    return resolve_tool("git", "GHPUBLISH_GIT", [
        home / "tools" / "git" / "cmd" / "git.exe",
        Path(r"C:\Program Files\Git\cmd\git.exe"),
        Path(r"C:\Program Files (x86)\Git\cmd\git.exe"),
    ])


def gh_path() -> str | None:
    home = Path.home()
    return resolve_tool("gh", "GHPUBLISH_GH", [
        home / "tools" / "gh" / "bin" / "gh.exe",
        Path(r"C:\Program Files\GitHub CLI\gh.exe"),
    ])


def run(args: list[str], cwd: Path | None = None, check: bool = True,
        capture: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE"):
        env.pop(key, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run(
        args, cwd=str(cwd) if cwd else None,
        text=True, encoding="utf-8", errors="replace",
        capture_output=capture, env=env,
    )
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        die(f"命令失败 ({res.returncode}): {' '.join(args)}\n{detail}")
    return res


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    exe = git_path() or die("未找到 git。运行 `ghpublish doctor` 查看安装位置。")
    return run([exe, *args], cwd=root, check=check)


def gh(*args: str, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    exe = gh_path() or die("未找到 gh (GitHub CLI)。运行 `ghpublish doctor` 查看安装位置。")
    return run([exe, *args], cwd=cwd, check=check)


def slugify(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "-", name.strip()).strip("-._")
    return s.lower() or "project"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def is_binary(sample: bytes) -> bool:
    return b"\x00" in sample[:4096]


def iter_files(root: Path):
    total = 0
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            total += 1
            total_bytes += st.st_size
            if total > MAX_FILES or total_bytes > MAX_SCAN_BYTES:
                return
            yield p, st


# ---------------------------------------------------------------- scan

def scan_project(root: Path) -> dict:
    langs: dict[str, int] = {}
    ext_counts: dict[str, int] = {}
    secrets: list[dict] = []
    sensitive_files: list[dict] = []
    large_files: list[dict] = []
    junk_files: list[dict] = []
    total_bytes = 0
    file_count = 0
    scanned_text = 0

    for p, st in iter_files(root):
        file_count += 1
        total_bytes += st.st_size
        rel = p.relative_to(root).as_posix()
        ext = p.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        lang = EXT_LANG.get(ext)
        if lang and lang not in ("Markdown",):
            langs[lang] = langs.get(lang, 0) + 1

        base = p.name.lower()
        if st.st_size > LARGE_FILE_BYTES:
            large_files.append({"path": rel, "size": st.st_size})

        sev = None
        if base in dict(SENSITIVE_NAMES):
            sev = dict(SENSITIVE_NAMES)[base]
        elif ext in SENSITIVE_EXTS:
            sev = SENSITIVE_EXTS[ext]
        elif base.startswith(".env.") and base != ".env.example":
            sev = "critical"
        if sev:
            sensitive_files.append({"path": rel, "severity": sev})

        if ext in JUNK_EXTS and st.st_size > 1024 * 1024:
            junk_files.append({"path": rel, "size": st.st_size})

        if st.st_size > MAX_TEXT_BYTES or scanned_text > 60 * 1024 * 1024:
            continue
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp4",
                   ".mp3", ".woff", ".woff2", ".ttf", ".pdf"}:
            continue
        try:
            with p.open("rb") as fh:
                raw = fh.read(MAX_TEXT_BYTES)
        except OSError:
            continue
        if is_binary(raw):
            continue
        scanned_text += len(raw)
        try:
            text = raw.decode("utf-8", errors="ignore")
        except Exception:
            continue

        for name, pattern, pattern_sev in SECRET_PATTERNS:
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                token = m.group(0)
                # 明显占位符不算
                if re.search(r"(?i)(your|example|placeholder|xxx|dummy|change|test)[_-]?", token) and len(token) < 24:
                    continue
                secrets.append({
                    "path": rel, "line": line_no, "type": name,
                    "severity": pattern_sev,
                    "sample": token[:6] + "..." + token[-4:] if len(token) > 14 else "***",
                })
        # 高熵字符串(疑似密钥), 只看配置类小文件, 降低误报
        if ext in {".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".ps1", ".sh", ".env"}:
            for m in re.finditer(r"[A-Za-z0-9+/_\-]{32,200}", text):
                token = m.group(0)
                if entropy(token) > 4.5 and not token.startswith(("http", "C:\\", "/")):
                    line_no = text.count("\n", 0, m.start()) + 1
                    secrets.append({
                        "path": rel, "line": line_no, "type": "高熵字符串",
                        "severity": "warning",
                        "sample": token[:6] + "..." + token[-4:],
                    })

    # 去重 (同一文件同一行同一类型)
    seen = set()
    uniq = []
    for s in secrets:
        key = (s["path"], s["line"], s["type"])
        if key not in seen:
            seen.add(key)
            uniq.append(s)

    langs = dict(sorted(langs.items(), key=lambda kv: -kv[1]))
    critical = [s for s in uniq if s["severity"] == "critical"] + \
               [s for s in sensitive_files if s["severity"] == "critical"]
    warnings = [s for s in uniq if s["severity"] == "warning"] + \
               [s for s in sensitive_files if s["severity"] == "warning"]

    return {
        "root": str(root),
        "files": file_count,
        "bytes": total_bytes,
        "languages": langs,
        "extensions": dict(sorted(ext_counts.items(), key=lambda kv: -kv[1])[:12]),
        "secrets": uniq[:200],
        "sensitive_files": sensitive_files[:200],
        "large_files": large_files[:50],
        "junk_files": junk_files[:50],
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "is_repo": (root / ".git").exists(),
        "has_readme": any((root / n).exists() for n in ("README.md", "readme.md", "README.rst")),
        "has_license": any((root / n).exists() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt")),
        "has_gitignore": (root / ".gitignore").exists(),
        "has_ci": any((root / d).exists() for d in (".github/workflows",)),
    }


def print_scan(report: dict) -> None:
    out(f"项目: {report['root']}")
    out(f"文件: {report['files']} 个, 合计 {human_size(report['bytes'])}")
    out(f"语言: " + (", ".join(f"{k}({v})" for k, v in report["languages"].items()) or "未识别"))
    flags = []
    flags.append("已初始化 git" if report["is_repo"] else "未初始化 git")
    flags.append("有 README" if report["has_readme"] else "缺 README")
    flags.append("有 LICENSE" if report["has_license"] else "缺 LICENSE")
    flags.append("有 .gitignore" if report["has_gitignore"] else "缺 .gitignore")
    out("状态: " + ", ".join(flags))

    if report["critical_count"]:
        out(f"\n[阻止] 发现 {report['critical_count']} 处严重敏感信息, 发布前必须处理:")
        shown = 0
        for s in report["secrets"]:
            if s["severity"] == "critical" and shown < 25:
                out(f"  ! {s['path']}:{s['line']}  {s['type']}  {s['sample']}")
                shown += 1
        for s in report["sensitive_files"]:
            if s["severity"] == "critical" and shown < 25:
                out(f"  ! {s['path']}  (敏感文件)")
                shown += 1
        if report["critical_count"] > 25:
            out(f"  ... 还有 {report['critical_count'] - 25} 处")
    warns = [s for s in report["secrets"] if s["severity"] == "warning"]
    warn_files = [s for s in report["sensitive_files"] if s["severity"] == "warning"]
    if warns or warn_files:
        by_type: dict[str, int] = {}
        for w in warns:
            by_type[w["type"]] = by_type.get(w["type"], 0) + 1
        out(f"\n[警告] {report['warning_count']} 处需要人工确认: " +
            ", ".join(f"{k}×{v}" for k, v in by_type.items()) +
            (f", 敏感文件×{len(warn_files)}" if warn_files else ""))
        for w in warns[:10]:
            out(f"  ? {w['path']}:{w['line']}  {w['type']}  {w['sample']}")
        if len(warns) > 10:
            out(f"  ... 还有 {len(warns) - 10} 处高熵/赋值告警 (多为哈希或 base64, 需人工判断)")
        for w in warn_files[:10]:
            out(f"  ? {w['path']}  (敏感文件)")
    if report["large_files"]:
        out("\n[大文件] GitHub 单文件上限 100MB, 超过 50MB 会警告:")
        for f in report["large_files"]:
            out(f"  - {f['path']}  {human_size(f['size'])}")
    if report["junk_files"]:
        out("\n[垃圾文件] 建议排除:")
        for f in report["junk_files"][:15]:
            out(f"  - {f['path']}  {human_size(f['size'])}")
    if not report["critical_count"]:
        out("\n体检通过, 可以进入 init / publish 步骤。")


def cmd_scan(args) -> None:
    root = Path(args.path).resolve()
    if not root.is_dir():
        die(f"目录不存在: {root}")
    report = scan_project(root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_scan(report)
    if report["critical_count"] and not args.allow_secrets:
        sys.exit(2)


PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "requirements.txt", "package.json", "go.mod",
    "Cargo.toml", "pom.xml", "build.gradle", "CMakeLists.txt", "Makefile",
    "docker-compose.yml", "Dockerfile",
)


def looks_like_project(d: Path) -> bool:
    if any((d / m).exists() for m in PROJECT_MARKERS):
        return True
    if (d / ".git").exists() or any(d.glob("*.sln")) or any(d.glob("*.csproj")):
        return True
    # 没有依赖清单时, 按同语言源文件数量判断
    counts: dict[str, int] = {}
    for idx, (p, _) in enumerate(iter_files(d)):
        if idx > 400:
            break
        lang = EXT_LANG.get(p.suffix.lower())
        if lang and lang not in ("Markdown", "HTML", "CSS"):
            counts[lang] = counts.get(lang, 0) + 1
            if counts[lang] >= 3:
                return True
    return False


def cmd_list(args) -> None:
    base = Path(args.path).resolve()
    if not base.is_dir():
        die(f"目录不存在: {base}")
    rows = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in SKIP_DIRS:
            continue
        if d.name.startswith("-"):
            continue
        if not looks_like_project(d):
            continue
        rep = scan_project(d)
        rows.append((d, rep))
    rows.sort(key=lambda r: -r[1]["bytes"])

    if args.json:
        print(json.dumps([{
            "path": str(d), "name": d.name,
            "languages": rep["languages"], "files": rep["files"],
            "bytes": rep["bytes"], "is_repo": rep["is_repo"],
            "has_readme": rep["has_readme"], "critical": rep["critical_count"],
        } for d, rep in rows], ensure_ascii=False, indent=2))
        return

    if not rows:
        out("没有找到项目目录 (需要 package.json / pyproject.toml / go.mod / Cargo.toml / .git 等标记)")
        return
    out(f"{'项目':40} {'语言':22} {'文件':>5} {'大小':>8}  git README 严重项")
    for d, rep in rows:
        langs = "/".join(list(rep["languages"])[:3]) or "-"
        out(f"{d.name[:40]:40} {langs[:22]:22} {rep['files']:>5} {human_size(rep['bytes']):>8}  "
            f"{'Y' if rep['is_repo'] else '-':>3} {'Y' if rep['has_readme'] else '-':>6} "
            f"{rep['critical_count']:>3}")


# ---------------------------------------------------------------- init

def detect_languages(root: Path) -> list[str]:
    return list(scan_project(root)["languages"].keys())


def write_if_absent(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def build_gitignore(langs: list[str]) -> str:
    parts = [SECRET_GITIGNORE.strip(), GENERIC_GITIGNORE.strip()]
    for lang in langs:
        block = LANG_GITIGNORE.get(lang, "").strip()
        if block:
            parts.append(f"# ---- {lang} ----\n{block}")
    return "\n\n".join(parts) + "\n"


MIT_TEMPLATE = """MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def readme_skeleton(name: str, description: str, langs: list[str]) -> str:
    lang_line = " / ".join(langs) if langs else "通用"
    return f"""# {name}

> 一句话说明这个项目解决什么问题。{description}

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 功能

- 功能一: 说明它做什么
- 功能二: 说明它做什么

## 环境要求

- {lang_line}

## 安装

```bash
git clone https://github.com/USER/{name}.git
cd {name}
# 安装依赖
```

## 使用

```bash
# 最小可运行示例
```

## 项目结构

```
{name}/
├── ...
└── README.md
```

## 许可证

[MIT](LICENSE)
"""


def git_identity(root: Path) -> tuple[str, str]:
    cfg = load_config()

    def git_value(key: str) -> str:
        for scope in ("--local", "--global"):
            res = git(root, "config", scope, key, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        return ""

    name = git_value("user.name") or cfg.get("author") or os.environ.get("GHPUBLISH_AUTHOR") or getuser()
    email = git_value("user.email") or cfg.get("email") or ""
    if not email or "@" not in email:
        email = f"{name}@users.noreply.github.com"
    return name, email


def ensure_repo(root: Path, branch: str = "main") -> bool:
    created = False
    if not (root / ".git").exists():
        git(root, "init", "-b", branch)
        created = True
    name, email = git_identity(root)
    local_name = git(root, "config", "user.name", check=False).stdout.strip()
    if not local_name:
        git(root, "config", "user.name", name)
    local_email = git(root, "config", "user.email", check=False).stdout.strip()
    if not local_email:
        git(root, "config", "user.email", email)
    return created


def commit_if_dirty(root: Path, message: str) -> bool:
    git(root, "add", "-A")
    staged = git(root, "diff", "--cached", "--name-only", check=False).stdout.strip()
    if not staged:
        return False
    git(root, "commit", "-m", message)
    return True


def cmd_init(args) -> None:
    root = Path(args.path).resolve()
    if not root.is_dir():
        die(f"目录不存在: {root}")
    report = scan_project(root)
    langs = list(report["languages"].keys())
    name = args.name or slugify(root.name)
    done = []

    created = ensure_repo(root, args.branch)
    if created:
        done.append(f"git init (分支 {args.branch})")
    if getattr(args, "author", None):
        git(root, "config", "user.name", args.author)
    if getattr(args, "email", None):
        git(root, "config", "user.email", args.email)
    author, email = git_identity(root)
    year = datetime.now().year

    if write_if_absent(root / ".gitignore", build_gitignore(langs)):
        done.append(".gitignore")
    license_id = (args.license or load_config().get("license") or "MIT").upper()
    if license_id != "NONE":
        if write_if_absent(root / "LICENSE", MIT_TEMPLATE.format(year=year, author=author)):
            done.append("LICENSE (MIT)")
    if write_if_absent(root / "README.md", readme_skeleton(name, args.desc or "", langs)):
        done.append("README.md")
    else:
        out("README.md 已存在, 保留原文件")

    committed = commit_if_dirty(root, args.message or "chore: initial commit")
    if committed:
        done.append("首次提交")
    elif not (root / ".git").exists():
        die("git 初始化失败")

    out("已生成: " + (", ".join(done) if done else "无新增文件"))
    out(f"git 身份: {author} <{email}>")
    out("\n下一步: 编辑 README.md 写清项目介绍, 然后运行")
    out(f"  ghpublish publish \"{root}\" --name {name} --desc \"...\" ")


# ---------------------------------------------------------------- publish

def gh_authenticated() -> bool:
    res = gh("auth", "status", check=False)
    return res.returncode == 0


def cmd_doctor(args) -> None:
    cfg = load_config()
    git_exe, gh_exe = git_path(), gh_path()
    out(f"git : {git_exe or '未找到'}")
    if git_exe:
        out("      " + run([git_exe, "--version"], check=False).stdout.strip())
    out(f"gh  : {gh_exe or '未找到'}")
    if gh_exe:
        out("      " + run([gh_exe, "--version"], check=False).stdout.strip().splitlines()[0])
    ok_auth = False
    if gh_exe:
        res = gh("auth", "status", check=False)
        ok_auth = res.returncode == 0
        first = (res.stdout or res.stderr or "").strip().splitlines()
        out("auth: " + ("已登录 " + (first[0] if first else "") if ok_auth else "未登录"))
    out(f"配置: {CONFIG_FILE}" + (" (存在)" if CONFIG_FILE.exists() else " (无)"))
    if cfg.get("author"):
        out(f"作者: {cfg['author']} <{cfg.get('email', '')}>")

    if not ok_auth:
        out("\n登录 GitHub (一次性, 需要浏览器/设备码):")
        out(f'  "{gh_exe}" auth login -h github.com -p https -w')
        out("或设置环境变量 GH_TOKEN 为 Personal Access Token。")
    if not git_exe or not gh_exe:
        out("\n工具链不完整, 重新运行安装脚本 install.ps1。")
        sys.exit(1)


def remote_url(root: Path) -> str | None:
    res = git(root, "remote", "get-url", "origin", check=False)
    return res.stdout.strip() if res.returncode == 0 else None


def cmd_publish(args) -> None:
    root = Path(args.path).resolve()
    if not root.is_dir():
        die(f"目录不存在: {root}")
    name = slugify(args.name or root.name)
    visibility = "public" if args.public else "private"
    report = scan_project(root)

    if report["critical_count"] and not args.allow_secrets:
        print_scan(report)
        die("发现严重敏感信息 (密钥/私钥/凭据), 已中止。处理后再发布, 或显式 --allow-secrets。")
    if report["large_files"] and max(f["size"] for f in report["large_files"]) > 100 * 1024 * 1024:
        die("存在超过 100MB 的文件, GitHub 会拒绝推送。请先排除或使用 Git LFS。")

    authed = gh_authenticated()

    if not (root / ".git").exists():
        if args.confirm:
            cmd_init(argparse.Namespace(
                path=str(root), name=name, desc=args.desc, license=args.license,
                author=None, email=None, branch="main", message=None))
        else:
            out("[计划] 尚未初始化 git, 将自动执行 init (生成 .gitignore/LICENSE/README)")
    else:
        commit_if_dirty(root, args.message or "chore: update before publish")

    existing = remote_url(root)
    plan = []
    if existing:
        plan.append(f"推送到已有 origin: {existing}")
    else:
        plan.append(f"创建仓库 {visibility}: {args.owner + '/' if args.owner else ''}{name}")
        plan.append("推送当前分支到 origin")
    if args.desc:
        plan.append(f"设置仓库简介: {args.desc}")
    if args.topics:
        plan.append(f"设置 topics: {args.topics}")
    if not authed:
        plan.append("需要先登录: ghpublish doctor 查看登录命令")

    out("\n发布计划:")
    for item in plan:
        out("  - " + item)
    out(f"  可见性: {visibility.upper()}")
    if args.public:
        out("  注意: 公开仓库会立即对全网可见并被搜索引擎/镜像站收录, 内容无法真正撤回。")

    if not args.confirm:
        out("\n未执行。确认无误后加 --confirm 重新运行。")
        return

    if not authed:
        die("gh 未登录, 无法发布。运行 `ghpublish doctor` 查看登录命令。")

    if not existing:
        create = ["repo", "create",
                  f"{args.owner + '/' if args.owner else ''}{name}",
                  "--source", str(root), "--remote", "origin", "--push",
                  f"--{visibility}"]
        if args.desc:
            create += ["--description", args.desc]
        res = gh(*create, check=False, cwd=root)
        if res.returncode != 0:
            die(f"创建仓库失败:\n{(res.stderr or res.stdout).strip()}")
    else:
        git(root, "push", "-u", "origin", "HEAD")

    full = args.owner + "/" + name if args.owner else None
    if args.topics:
        topic_args = ["repo", "edit"]
        topic_args.append(full or name)
        for t in [x.strip() for x in args.topics.split(",") if x.strip()]:
            topic_args += ["--add-topic", t]
        gh(*topic_args, check=False)
    if args.desc and existing:
        gh("repo", "edit", full or name, "--description", args.desc, check=False)

    res = gh("repo", "view", full or name, "--json", "url,visibility", check=False)
    url = ""
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            url = data.get("url", "")
            visibility = data.get("visibility", visibility)
        except Exception:
            pass
    out(f"\n完成: {url or '(无法读取 URL)'}  可见性: {visibility}")


def cmd_status(args) -> None:
    root = Path(args.path).resolve()
    if not (root / ".git").exists():
        out("不是 git 仓库")
        return
    out("分支: " + git(root, "branch", "--show-current", check=False).stdout.strip())
    out("远端: " + (remote_url(root) or "无"))
    out("提交: " + git(root, "rev-list", "--count", "HEAD", check=False).stdout.strip() + " 个")
    dirty = git(root, "status", "--porcelain", check=False).stdout.strip()
    out("工作区: " + ("有未提交修改" if dirty else "干净"))


# ---------------------------------------------------------------- cli

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="ghpublish", description="把本地项目开源到 GitHub")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="检查 git / gh / 登录状态")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("scan", help="发布前体检")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.add_argument("--allow-secrets", action="store_true", help="发现问题也返回 0")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("list", help="列出目录下的候选项目")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("init", help="生成开源所需文件并初始化 git")
    p.add_argument("path")
    p.add_argument("--name", help="仓库名, 默认取目录名")
    p.add_argument("--desc", help="仓库简介")
    p.add_argument("--license", default=None, help="MIT / NONE, 默认 MIT")
    p.add_argument("--branch", default="main")
    p.add_argument("--author")
    p.add_argument("--email")
    p.add_argument("--message")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("publish", help="创建 GitHub 仓库并推送")
    p.add_argument("path")
    p.add_argument("--name", help="仓库名, 默认取目录名")
    p.add_argument("--owner", help="组织/用户名, 默认当前登录账号")
    p.add_argument("--desc", help="仓库简介")
    p.add_argument("--topics", help="逗号分隔, 如 cli,python,tool")
    p.add_argument("--public", action="store_true", help="公开仓库 (默认私有)")
    p.add_argument("--license", default=None)
    p.add_argument("--allow-secrets", action="store_true")
    p.add_argument("--confirm", action="store_true", help="真正执行; 不加只打印计划")
    p.add_argument("--message")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("status", help="查看仓库状态")
    p.add_argument("path")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
