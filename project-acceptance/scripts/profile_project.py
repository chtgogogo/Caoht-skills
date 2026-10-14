#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目画像脚本 —— 项目验收官 skill 的 Phase 0 工具。

用法:
    python profile_project.py <项目根目录> [--max-depth 8] [--max-files 20000]
        [--max-language-files 3000] [--max-credential-files 6000] [--strict-coverage]

输出: 一份 JSON 画像(语言/框架/项目类型猜测/工具链/红旗/疑似凭据/覆盖范围声明),
     供验收官决定哪些检查项适用、动态启发从哪里下手。纯标准库, 无第三方依赖。

两条通道各自独立(语言分析 ≠ 凭据扫描, 各自的覆盖面与上限互不影响):
  - language_scan    : 只看源码扩展名, 统计语言与工程红旗
  - credential_scan  : 源码 + 配置文件(.json/.yaml/...) + 无扩展名的特定文件名(.env 等)
                       命中即**生成时脱敏**, 输出里不落明文凭据

本脚本是"初筛"不是"终判":
  - 疑似凭据必须人工逐条确认后才可写进报告;
  - 自带正则是**初筛**(覆盖面有限), 建议补用成熟扫描组件 gitleaks / detect-secrets /
    TruffleHog(见 SKILL.md §8), 二者结论不一致时以更严者为准;
  - **未扫到 ≠ 没有**: 凡 coverage.insufficient = true, 报告不得写"凭据扫描通过",
    只能写"覆盖范围内未初筛命中"并把未检查清单抄进报告。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import Counter

# ---------------------------------------------------------------- 目录与扩展名
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".idea", ".vscode", "dist", "build", ".next", ".nuxt", "target",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", ".tox",
    "site-packages", ".gradle", "bin", "obj", "vendor",
}
# 语言分析通道认可的扩展名（源码）
SOURCE_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "typescript",
    ".go": "go", ".java": "java", ".rb": "ruby", ".php": "php",
    ".rs": "rust", ".c": "c", ".cpp": "c++", ".cs": "c#",
    ".sql": "sql", ".sh": "shell", ".ps1": "powershell", ".bat": "batch",
    ".gd": "gdscript", ".vue": "vue", ".svelte": "svelte",
}
# 凭据扫描通道的配置文件扩展名（与 SOURCE_EXT 取并集，不再被语言分析的门禁掉）
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
              ".properties", ".env", ".xml", ".lock"}
# 凭据扫描通道的纯文本扩展名（README/文档里贴了真 key 是真实事故形态）
TEXT_EXT = {".md", ".txt", ".rst"}
# 明确按文件名（而非扩展名）纳入凭据扫描的条目——.env / 私钥 / 凭证缓存多为无扩展名或怪扩展名
CREDENTIAL_NAME_PREFIXES = (
    ".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
)
CREDENTIAL_NAME_SUFFIXES = (
    ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".credentials", ".env",
)
# 凭据扫描通道明确排除的二进制/数据文件（计入未检查清单，不静默消失）
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar",
    ".pyc", ".pyd", ".so", ".dll", ".dylib", ".exe", ".class", ".jar", ".war",
    ".db", ".sqlite", ".sqlite3", ".npy", ".npz", ".pkl", ".pt", ".pth",
    ".onnx", ".safetensors", ".h5", ".parquet", ".arrow", ".feather", ".bin",
    ".iso", ".dmg", ".whl",
}

# ---------------------------------------------------------------- 项目结构线索
MANIFEST_NAMES = {
    "requirements.txt", "requirements-dev.txt", "requirements_base.txt",
    "pyproject.toml", "setup.py", "Pipfile", "package.json", "go.mod",
    "Cargo.toml", "pom.xml", "build.gradle", "composer.json", "Gemfile",
    "project.godot", "pubspec.yaml",
}
LOCKFILE_NAMES = [
    "poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "go.sum", "Cargo.lock", "composer.lock", "Gemfile.lock",
]
CI_PATHS = [
    ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile",
    ".circleci/config.yml", "azure-pipelines.yml", ".drone.yml",
]
LINT_HINTS = [
    ".ruff.toml", "ruff.toml", ".flake8", ".pylintrc", ".eslintrc",
    "eslint.config.js", "eslint.config.mjs", ".eslintrc.json", ".eslintrc.js",
    ".prettierrc", ".prettierrc.json", "biome.json", ".golangci.yml",
]
PY_FRAMEWORKS = [
    "fastapi", "flask", "django", "gradio", "streamlit", "langchain",
    "llama-index", "llama_index", "llamaindex", "chromadb", "qdrant-client",
    "pymilvus", "faiss", "sentence-transformers", "openai", "anthropic",
    "zhipuai", "dashscope", "autogen", "crewai", "pyautogen", "airflow",
    "dbt", "prefect", "celery", "click", "typer", "uvicorn", "gunicorn",
    "sqlalchemy", "scrapy", "paddleocr", "torch", "tensorflow",
]
JS_FRAMEWORKS = [
    "express", "next", "nuxt", "vue", "react", "svelte", "vite", "webpack",
    "nest", "@nestjs/core", "koa", "fastify", "commander", "yargs",
    "langchain", "openai", "@anthropic-ai/sdk", "playwright", "puppeteer",
    "electron", "prisma", "tailwindcss",
]
GO_FRAMEWORKS = ["gin-gonic/gin", "labstack/echo", "gofiber/fiber", "spf13/cobra"]
RUST_FRAMEWORKS = ["actix", "axum", "clap", "warp"]
VECTOR_HINTS = ["chromadb", "qdrant", "milvus", "faiss", "weaviate", "pgvector", "pinecone"]
LLM_HINTS = ["openai", "anthropic", "zhipuai", "dashscope", "langchain", "llama"]

# ---------------------------------------------------------------- 凭据初筛规则
# 条目 = (规则名, 编译好的正则, 值所在的捕获组号[0=整个匹配即敏感串], 允许的信道[None=全部])
# 前 9 条是「格式特征」类（sk-/ghp_/AKIA/PEM…）：命中即高可信，任何信道都算。
# 后 2 条是「键名+赋值」类：必须再过值形态闸（见 value_looks_credential），否则真实仓库里
# `token = idm.group(1)` 和文档里的中文示例值都会被当成凭据——初筛一旦噪音淹没人工复核，
# 就等于没有。两类赋值规则的分工：
#   quoted_assignment：值带引号 → 源码/配置/文档通吃（代码里的硬编码字面量必然带引号）
#   unquoted_assignment：值不带引号（.env / .ini / properties / 文档粘贴的真实形态）
#     → **源码信道明确排除**：Python/JS/Go 里等号右侧不带引号的一定是变量引用而不是密钥
#       字面量，在这一信道启用只会制造误报；源码里的真凭据由上面两类规则负责，不靠这条兜底。
_ASSIGN_KEYS = (r'''api[_-]?key|apikey|secret|token|passwd|password|pwd'''
                r'''|access[_-]?key|credential|client[_-]?secret''')
NON_SOURCE_CHANNELS = ("special-name", "config", "text", "other")
CREDENTIAL_PATTERNS = [
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0, None),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}"), 0, None),
    ("anthropic_style_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), 0, None),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), 0, None),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), 0, None),
    ("stripe_live_key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}"), 0, None),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), 0, None),
    ("private_key_block", re.compile(r"-----BEGIN\s+(?:[A-Z]+\s+)*PRIVATE\s+KEY-----"), 0, None),
    ("jwt_token", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), 0, None),
    # 带引号的赋值（Python/JS/JSON/YAML 皆覆盖）；组 1 = 完整键名，组 2 = 值
    ("quoted_assignment", re.compile(
        r'''(?i)([\w.\-]*?(?:''' + _ASSIGN_KEYS + r''')[\w.\-]*)'''
        r'''["']?\s*[=:]\s*["']([^"']{12,})["']'''), 2, None),
    # 无引号赋值：dotenv 的 KEY=value（含 export 前缀）、ini/cfg/properties 的 key = value、
    # YAML 里不带引号的 key: value 都走这一条；键名大小写都收（真凭据在 .env 里常是全大写，
    # 在 .ini 里常是小写），等号两侧允许空格，值里不许有空格/引号/Tab。
    ("unquoted_assignment", re.compile(
        r'''(?i)^[ \t]*(?:export[ \t]+)?'''
        r'''([\w.\-]*?(?:''' + _ASSIGN_KEYS + r''')[\w.\-]*)'''
        r'''[ \t]*[=:][ \t]*([^"' \t]{8,})[ \t]*$'''), 2, NON_SOURCE_CHANNELS),
]
PLACEHOLDER_WORDS = re.compile(
    r"(?i)(xxx|your[_-]?|example|placeholder|dummy|sample|changeme|todo|n/a|<[^>]+>)")
# 明显的假值/文档示例特征：命中后计数但不作为发现上报（计数进 coverage，不许静默丢）
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
_LITERAL_RE = re.compile(r"[A-Za-z0-9_\-+/=.:%@~!#&*]{8,}")


def value_looks_credential(v):
    """赋值型命中的第二道闸：值必须「长得像凭据」，不像就降级或丢弃。

    被挡掉的三类真实误报：① 代码表达式（`token = idm.group(1)`、`os.environ[...]`）
    ② 自然语言（中英说明句、`"请输入密码"`）③ 布尔/空值/占位常量。
    含空格但确实是字面量 passphrase 的值判为低可信，仍上报，交人工分级。
    """
    s = (v or "").strip()
    if len(s) < 8:
        return False, "too-short"
    if _CJK_RE.search(s):
        return False, "natural-language"
    if any(ch in s for ch in "()[]{}"):
        return False, "expression"
    if s.lower() in ("true", "false", "null", "none", "yes", "no", "undefined", "str"):
        return False, "constant"
    if re.match(r"""^['"]?[A-Za-z_][\w.]*(\(|\[[^\]]*\])""", s):
        return False, "expression"
    if re.match(r"^(\.env|\$|\{|\[)", s):
        return False, "template"
    if " " in s:
        return True, "spaced-passphrase"
    if not _LITERAL_RE.fullmatch(s):
        return False, "not-literal"
    return True, "literal"


def finding_confidence(val_group, shape_reason):
    """分级是给人工复核排队用的：高=格式特征直接命中；中=字面量赋值；低=带空格的口令短语。
    按「值来自哪个捕获组」判，不按规则名判——规则表会增删改名，名字清单会静默过期。"""
    if val_group == 0:
        return "高"
    return "低" if shape_reason == "spaced-passphrase" else "中"


SECRET_CHANNEL_LABEL = {
    "source": "源码", "config": "配置文件", "text": "文本文档",
    "special-name": "特定文件名（.env/私钥/凭证缓存）", "other": "其他文本文件",
}

BARE_EXCEPT_RE = re.compile(r"except\s*(Exception)?\s*:")
PRINT_RE = re.compile(r"^\s*print\(")
CONSOLE_LOG_RE = re.compile(r"console\.log\(")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
CLI_PY_RE = re.compile(r"^\s*(import|from)\s+(argparse|click|typer)\b", re.M)

DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_FILES = 20000
DEFAULT_MAX_LANGUAGE_FILES = 3000
DEFAULT_MAX_CREDENTIAL_FILES = 6000
DEFAULT_MAX_READ_BYTES = 256 * 1024
LIST_CAP = 50          # 未检查清单在 JSON 里最多列这么多条，其余只报数量
FINDINGS_CAP = 40      # 疑似凭据最多上报条数


# ---------------------------------------------------------------- 基础工具
def rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def mask_secret(raw):
    """把敏感串就地脱敏：只留前 3 个字符与后 2 个字符，中间打码，并附长度。

    判据：任何长度的值都不会在输出里以明文出现；长度信息够人工定位用。
    """
    s = raw.strip()
    n = len(s)
    if n <= 4:
        return "*" * n + "(len=%d)" % n
    head = s[:3]
    tail = s[-2:] if n >= 10 else s[-1:]
    hidden = n - len(head) - len(tail)
    dots = "*" * min(hidden, 12) + ("…" if hidden > 12 else "")
    return "%s%s%s(len=%d)" % (head, dots, tail, n)


def read_text(path, max_bytes):
    """读文本，返回 (text, skip_reason)。

    三种跳过理由都会进未检查清单——静默跳过是「没有发现」被误读成「扫描完整」的入口。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "stat-failed"
    if size > max_bytes:
        return None, "too-large"
    try:
        with open(path, "rb") as f:
            head = f.read(min(8192, max_bytes))
            if b"\x00" in head:
                return None, "binary"
            body = f.read(max_bytes - len(head)) if size > len(head) else b""
    except OSError:
        return None, "read-failed"
    return (head + body).decode("utf-8", errors="ignore"), None


def safe_read(path, max_bytes=DEFAULT_MAX_READ_BYTES):
    text, _ = read_text(path, max_bytes)
    return text or ""


def run_git(root, *args):
    try:
        out = subprocess.run(
            ["git", "-C", root] + list(args), capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="ignore")
        return out.returncode, (out.stdout or "").strip(), (out.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, "", "git unavailable"


# ---------------------------------------------------------------- 目录遍历（带覆盖统计）
def count_tree(path, limit=2000):
    """数一棵被剪掉的子树里有多少文件（带上限，宁可报「≥」也不报 0）。"""
    n, capped = 0, False
    for _base, _dirs, names in os.walk(path):
        n += len(names)
        if n >= limit:
            capped = True
            break
    return n, capped


def walk_project(root, max_files=DEFAULT_MAX_FILES, max_depth=DEFAULT_MAX_DEPTH):
    files = []
    st = {
        "max_files": max_files,
        "max_depth": max_depth,
        "files_seen": 0,
        "files_collected": 0,
        "files_excluded_by_file_cap": 0,
        "files_excluded_by_depth_cap": 0,
        "dirs_pruned_by_depth": [],
        "dirs_skipped_by_name": [],
    }
    cap_hit = False
    for base, dirs, names in os.walk(root):
        rel_base = rel(root, base)
        if rel_base == ".":
            rel_base = ""
        depth = 0 if not rel_base else rel_base.count("/") + 1
        kept = []
        for d in sorted(dirs):
            child = "%s/%s" % (rel_base, d) if rel_base else d
            if d in SKIP_DIRS or d == ".git":
                st["dirs_skipped_by_name"].append(child)
                continue
            if depth + 1 > max_depth:
                cnt, capped = count_tree(os.path.join(base, d))
                st["dirs_pruned_by_depth"].append(
                    {"dir": child, "depth": depth + 1, "files": cnt, "files_capped": capped})
                st["files_excluded_by_depth_cap"] += cnt
                continue
            kept.append(d)
        dirs[:] = kept
        if depth > max_depth:
            st["files_excluded_by_depth_cap"] += len(names)
            continue
        for n in names:
            st["files_seen"] += 1
            if cap_hit:
                st["files_excluded_by_file_cap"] += 1
                continue
            files.append(os.path.join(base, n))
            if len(files) >= max_files:
                cap_hit = True
    st["files_collected"] = len(files)
    return files, st


# ---------------------------------------------------------------- 语言分析通道
def language_scan(root, files, max_files=DEFAULT_MAX_LANGUAGE_FILES,
                  max_bytes=DEFAULT_MAX_READ_BYTES):
    """只看源码扩展名：语言计数 + 工程红旗。与凭据扫描完全解耦。"""
    langs, ext_file_count = Counter(), Counter()
    hits = {"cli_python": 0, "print_py_prod": [], "console_log": 0,
            "bare_except": [], "todo": 0}
    st = {"cap": max_files, "scanned": 0, "candidates": 0, "truncated": False,
          "excluded_files": []}
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        if ext not in SOURCE_EXT:
            continue
        st["candidates"] += 1
        ext_file_count[SOURCE_EXT[ext]] += 1
        if st["scanned"] >= max_files:
            st["truncated"] = True
            if len(st["excluded_files"]) < LIST_CAP:
                st["excluded_files"].append({"file": rel(root, fp), "reason": "语言分析文件数上限"})
            continue
        content, skip = read_text(fp, max_bytes)
        if skip:
            if len(st["excluded_files"]) < LIST_CAP:
                st["excluded_files"].append({"file": rel(root, fp), "reason": skip})
            continue
        st["scanned"] += 1
        r = os.path.relpath(fp, root).replace("\\", "/")
        low_rel = r.lower()
        parts = low_rel.split("/")
        is_test = any(p.startswith("test") or p in ("tests", "__tests__", "spec")
                      for p in parts) or bool(re.search(r"(test_|_test\.|\.test\.)", low_rel))
        if ext == ".py" and not is_test:
            if CLI_PY_RE.search(content):
                hits["cli_python"] += 1
            for i, line in enumerate(content.splitlines(), 1):
                if PRINT_RE.match(line):
                    hits["print_py_prod"].append("%s:%d" % (r, i))
        if ext in (".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte") and not is_test:
            hits["console_log"] += len(CONSOLE_LOG_RE.findall(content))
        if ext == ".py":
            for i, line in enumerate(content.splitlines(), 1):
                if BARE_EXCEPT_RE.search(line):
                    hits["bare_except"].append("%s:%d" % (r, i))
        hits["todo"] += len(TODO_RE.findall(content))
    hits["print_py_prod"] = hits["print_py_prod"][:15]
    hits["bare_except"] = hits["bare_except"][:15]
    langs.update({k: v for k, v in ext_file_count.items()})
    return langs, hits, st


# ---------------------------------------------------------------- 凭据扫描通道
def credential_channel(path):
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1].lower()
    if name.startswith(CREDENTIAL_NAME_PREFIXES) or name.endswith(CREDENTIAL_NAME_SUFFIXES):
        return "special-name"
    if ext in SOURCE_EXT:
        return "source"
    if ext in CONFIG_EXT:
        return "config"
    if ext in TEXT_EXT:
        return "text"
    if ext in BINARY_EXT:
        return None
    return "other"


def is_credential_candidate(path):
    return credential_channel(path) is not None


def credential_scan(root, files, max_files=DEFAULT_MAX_CREDENTIAL_FILES,
                    findings_cap=FINDINGS_CAP, max_bytes=DEFAULT_MAX_READ_BYTES):
    """源码 + 配置 + 文本 + 特定文件名全覆盖；命中即脱敏，绝不输出明文。"""
    st = {
        "cap": max_files,
        "read_cap": max_bytes,
        "channel_label": SECRET_CHANNEL_LABEL,
        "files_in_scope": 0,
        "scanned": 0,
        "truncated": False,
        "skipped": Counter(),
        "skipped_files": [],
        "shape_rejected": Counter(),
        "by_channel": Counter(),
        "placeholder_filtered": 0,
        "findings_total": 0,
        "findings_reported_cap": findings_cap,
        "findings_truncated": False,
    }
    findings = []
    for fp in files:
        ch = credential_channel(fp)
        if ch is None:
            st["skipped"]["binary-extension"] += 1
            if len(st["skipped_files"]) < LIST_CAP:
                st["skipped_files"].append({"file": rel(root, fp), "reason": "binary-extension"})
            continue
        st["files_in_scope"] += 1
        if st["scanned"] >= max_files:
            st["truncated"] = True
            continue
        content, skip = read_text(fp, max_bytes)
        if skip:
            st["skipped"][skip] += 1
            if len(st["skipped_files"]) < LIST_CAP:
                st["skipped_files"].append({"file": rel(root, fp), "reason": skip})
            continue
        st["scanned"] += 1
        st["by_channel"][ch] += 1
        r = rel(root, fp)
        for i, line in enumerate(content.splitlines(), 1):
            for pat_name, pat, val_group, allowed in CREDENTIAL_PATTERNS:
                if allowed is not None and ch not in allowed:
                    continue
                m = pat.search(line)
                if not m:
                    continue
                if PLACEHOLDER_WORDS.search(line):
                    st["placeholder_filtered"] += 1
                    break
                key_name = m.group(1).strip("\"'") if val_group >= 1 else ""
                raw_val = m.group(val_group) if val_group >= 1 else m.group(0)
                if val_group >= 1:
                    ok, shape = value_looks_credential(raw_val)
                    if not ok:
                        st["shape_rejected"][shape] += 1
                        continue
                else:
                    shape = "format-signature"
                findings.append({
                    "file": r,
                    "line": i,
                    "pattern": pat_name,
                    "channel": ch,
                    "channel_zh": SECRET_CHANNEL_LABEL[ch],
                    "confidence": finding_confidence(val_group, shape),
                    "value_shape": shape,
                    "key": key_name or None,
                    # 生成即脱敏：报告里只有掩码串，人工确认靠 file:line + key 名
                    "value_masked": mask_secret(raw_val),
                    "note": "初筛命中，需人工逐条确认（本脚本不是终判）",
                })
                st["findings_total"] += 1
                break
    if len(findings) > findings_cap:
        st["findings_truncated"] = True
        findings = findings[:findings_cap]
    st["skipped"] = dict(st["skipped"])
    st["shape_rejected"] = dict(st["shape_rejected"])
    st["by_channel"] = dict(st["by_channel"])
    return findings, st


# ---------------------------------------------------------------- 覆盖范围判定
def build_coverage(walk_st, lang_st, cred_st):
    limits, unchecked_dirs = [], []
    if walk_st["files_excluded_by_file_cap"]:
        limits.append({"kind": "文件数上限", "cap": walk_st["max_files"],
                       "excluded": walk_st["files_excluded_by_file_cap"],
                       "detail": "遍历到的文件数触顶，超出部分一个字节都没看"})
    if walk_st["dirs_pruned_by_depth"]:
        limits.append({"kind": "目录深度上限", "cap": walk_st["max_depth"],
                       "excluded": walk_st["files_excluded_by_depth_cap"],
                       "detail": "%d 个子树因超过深度上限整棵不进入，其中 %d 个文件一个字节都没读"
                                 % (len(walk_st["dirs_pruned_by_depth"]),
                                    walk_st["files_excluded_by_depth_cap"])})
        unchecked_dirs += [e["dir"] for e in walk_st["dirs_pruned_by_depth"][:LIST_CAP]]
    if walk_st["dirs_skipped_by_name"]:
        limits.append({"kind": "跳过目录名单", "cap": len(SKIP_DIRS),
                       "excluded": len(walk_st["dirs_skipped_by_name"]),
                       "detail": "%d 个依赖/构建/工具目录按 SKIP_DIRS 整目录不进入"
                                 "（内部文件数未统计，目录名见未检查清单；注意叫 env 的目录也在名单里，"
                                 "项目里以目录形式存放的配置文件不会进凭据扫描）"
                                 % len(walk_st["dirs_skipped_by_name"])})
        unchecked_dirs += walk_st["dirs_skipped_by_name"][:LIST_CAP]
    if lang_st["truncated"]:
        limits.append({"kind": "语言分析文件数上限", "cap": lang_st["cap"],
                       "excluded": lang_st["candidates"] - lang_st["scanned"],
                       "detail": "源码红旗（print/裸 except/console.log/TODO）只统计了前 %d 个文件"
                                 % lang_st["scanned"]})
    if cred_st["truncated"]:
        limits.append({"kind": "凭据扫描文件数上限", "cap": cred_st["cap"],
                       "excluded": cred_st["files_in_scope"] - cred_st["scanned"],
                       "detail": "凭据初筛只读了前 %d 个文件，其余在名单内未读" % cred_st["scanned"]})
    for reason, cnt in sorted(cred_st["skipped"].items()):
        if cnt:
            limits.append({"kind": "凭据扫描读取跳过", "cap": cred_st.get("read_cap", 0),
                           "excluded": cnt,
                           "detail": "%s：%d 个在扫描范围内的文件没有被读到" % (reason, cnt)})
    if cred_st["findings_truncated"]:
        limits.append({"kind": "疑似凭据上报条数上限", "cap": cred_st["findings_reported_cap"],
                       "excluded": cred_st["findings_total"] - cred_st["findings_reported_cap"],
                       "detail": "命中太多，只列前 %d 条，其余按数量如实登记"
                                 % cred_st["findings_reported_cap"]})
    insufficient = bool(limits)
    decl = ""
    if insufficient:
        parts = ["%s（排除 %s 项，原因：%s）" % (l["kind"], l["excluded"], l["detail"])
                 for l in limits]
        decl = ("⚠️ 覆盖范围不足：%d 项限制被触发 ⇒ 本报告不得写「扫描通过/无凭据泄露」，"
                "只能写「覆盖范围内未命中」；未检查目录 %d 个。| %s"
                % (len(limits), len(unchecked_dirs), " ; ".join(parts)))
    else:
        decl = ("✅ 覆盖范围完整：本次遍历 %d 个文件，语言分析读 %d 个，凭据初筛读 %d 个，"
                "无上限触发、无读取跳过。" % (walk_st["files_seen"], lang_st["scanned"],
                                             cred_st["scanned"]))
    walk_out = {k: v for k, v in walk_st.items()}
    walk_out["dirs_skipped_by_name"] = walk_st["dirs_skipped_by_name"][:LIST_CAP]
    walk_out["dirs_skipped_by_name_total"] = len(walk_st["dirs_skipped_by_name"])
    walk_out["dirs_pruned_by_depth"] = walk_st["dirs_pruned_by_depth"][:LIST_CAP]
    walk_out["files_excluded_by_skip_dirs"] = "未统计（整目录不进入遍历）"
    return {
        "insufficient": insufficient,
        "declaration": decl,
        "limits_triggered": limits,
        "unchecked_dirs": unchecked_dirs,
        "unchecked_dirs_total": len(unchecked_dirs),
        "walk": walk_out,
        "language_scan": lang_st,
        "credential_scan": cred_st,
    }


# ---------------------------------------------------------------- 项目结构分析
def detect_frameworks(root, files):
    found = {}
    manifest_lower = {m.lower() for m in MANIFEST_NAMES}
    for fp in files:
        name = os.path.basename(fp).lower()
        if name not in manifest_lower:
            continue
        content = safe_read(fp, 400 * 1024)
        low = content.lower()
        r = rel(root, fp)
        if name == "package.json":
            try:
                data = json.loads(content or "{}")
            except json.JSONDecodeError:
                data = {}
            deps = {}
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                deps.update(data.get(section) or {})
            for fw in JS_FRAMEWORKS:
                if fw in deps:
                    found.setdefault(fw, []).append(r)
            if data.get("bin"):
                found.setdefault("js-bin-entry", []).append(r)
        elif name in ("requirements.txt", "requirements-dev.txt",
                      "requirements_base.txt", "pipfile"):
            for fw in PY_FRAMEWORKS:
                if re.search(r"(?im)^\s*[-a-z0-9_.]*" + re.escape(fw) + r"\b", low):
                    found.setdefault(fw, []).append(r)
        elif name in ("pyproject.toml", "setup.py"):
            for fw in PY_FRAMEWORKS:
                if re.search(r"(?i)[\"']" + re.escape(fw) + r"[\"']?", low):
                    found.setdefault(fw, []).append(r)
            if name == "pyproject.toml" and re.search(r"\[project\.scripts\]", low):
                found.setdefault("python-console-scripts", []).append(r)
        elif name == "go.mod":
            for fw in GO_FRAMEWORKS:
                if fw.split("/")[-1] in low or fw in low:
                    found.setdefault(fw, []).append(r)
        elif name == "cargo.toml":
            for fw in RUST_FRAMEWORKS:
                if fw in low:
                    found.setdefault(fw, []).append(r)
        elif name == "project.godot":
            found.setdefault("godot", []).append(r)
    return found


def guess_types(frameworks, hits):
    types = []
    fw = set(frameworks)
    ev_api = [f for f in fw if f in ("fastapi", "flask", "django", "express",
                                     "koa", "fastify", "nest", "@nestjs/core",
                                     "gin-gonic/gin", "labstack/echo", "gofiber/fiber",
                                     "gradio", "streamlit", "uvicorn", "gunicorn")]
    if ev_api:
        types.append({"type": "api-service", "zh": "Web服务/API", "evidence": ev_api,
                      "confidence": "high"})
    ev_front = [f for f in fw if f in ("react", "vue", "svelte", "vite", "webpack",
                                       "next", "nuxt", "tailwindcss", "electron")]
    if ev_front:
        types.append({"type": "frontend", "zh": "前端", "evidence": ev_front,
                      "confidence": "high" if ev_front != ["next"] else "medium"})
    llm = [f for f in fw if f in LLM_HINTS or any(h in f for h in LLM_HINTS)]
    vec = [f for f in fw if any(h in f for h in VECTOR_HINTS)]
    if "langchain" in fw or "autogen" in fw or "crewai" in fw or "pyautogen" in fw:
        types.append({"type": "agent", "zh": "Agent/LLM应用", "evidence": ["langchain系"],
                      "confidence": "medium"})
    if vec or ("llama-index" in fw or "llama_index" in fw or "llamaindex" in fw):
        types.append({"type": "rag", "zh": "RAG/知识库", "evidence": vec or ["llama-index"],
                      "confidence": "high"})
    elif llm:
        types.append({"type": "llm-app", "zh": "AI应用(调用LLM)", "evidence": llm,
                      "confidence": "medium"})
    if hits.get("cli_python", 0) > 0 or "python-console-scripts" in fw \
            or "js-bin-entry" in fw or "spf13/cobra" in fw or "clap" in fw:
        types.append({"type": "cli", "zh": "命令行工具", "confidence": "medium",
                      "evidence": ["argparse/click/typer 或入口声明"]})
    if "airflow" in fw or "dbt" in fw or "prefect" in fw or "celery" in fw:
        types.append({"type": "data-pipeline", "zh": "数据管道/定时任务",
                      "evidence": [f for f in fw if f in ("airflow", "dbt", "prefect", "celery")],
                      "confidence": "high"})
    if "godot" in fw:
        types.append({"type": "game", "zh": "游戏(Godot)", "evidence": ["project.godot"],
                      "confidence": "high"})
    has_web = any(t["type"] in ("api-service", "frontend") for t in types)
    has_cli = any(t["type"] == "cli" for t in types)
    if not types or (not has_web and not has_cli):
        types.append({"type": "library-or-script", "zh": "库/脚手架(待人工确认)",
                      "evidence": ["未命中服务/前端/CLI信号"], "confidence": "low"})
    return types


def check_git(root):
    rc, head, _ = run_git(root, "rev-parse", "HEAD")
    if rc != 0:
        return None
    _, porcelain, _ = run_git(root, "status", "--porcelain")
    dirty = [l for l in porcelain.splitlines() if l.strip()]
    _, last, _ = run_git(root, "log", "-1", "--format=%H%n%ci%n%s")
    lines = last.split("\n") if last else ["", "", ""]
    env_ignored_rc, _, _ = run_git(root, "check-ignore", "-q", ".env")
    env_exists = os.path.exists(os.path.join(root, ".env"))
    tracked_rc, tracked, _ = run_git(root, "ls-files", "--", "*.env")
    return {
        "is_repo": True,
        "head": lines[0] if lines else "",
        "last_commit": {"date": lines[1] if len(lines) > 1 else "",
                        "subject": lines[2] if len(lines) > 2 else ""},
        "uncommitted_changes": len(dirty),
        "dirty_sample": dirty[:10],
        "env_file_exists": env_exists,
        "env_gitignored": env_ignored_rc == 0,
        "env_tracked_by_git": bool(tracked.strip()),
    }


def build_profile(root, opts):
    files, walk_st = walk_project(root, opts.max_files, opts.max_depth)
    rel_files = [rel(root, f) for f in files]
    dir_names = {os.path.dirname(r) for r in rel_files}

    frameworks = detect_frameworks(root, files)
    langs, hits, lang_st = language_scan(root, files, opts.max_language_files,
                                         opts.max_read_bytes)
    suspected, cred_st = credential_scan(root, files, opts.max_credential_files,
                                         opts.max_findings, opts.max_read_bytes)
    coverage = build_coverage(walk_st, lang_st, cred_st)

    tests_dirs = [d for d in dir_names if os.path.basename(d).lower() in
                  ("tests", "test", "__tests__", "spec")]
    test_files = [r for r in rel_files if re.search(
        r"(^|/)(test_[^/]*\.py|[^/]*_test\.go|[^/]*\.test\.[jt]sx?)$", r.lower())]
    test_cfg = [r for r in rel_files if os.path.basename(r).lower() in
                ("pytest.ini", "jest.config.js", "jest.config.ts", "vitest.config.ts",
                 "vitest.config.js", "karma.conf.js")]
    lint_found = [r for r in rel_files if os.path.basename(r).lower() in
                  {h.lower() for h in LINT_HINTS}] + \
        ([r for r in rel_files if os.path.basename(r) == "pyproject.toml"
          and "ruff" in safe_read(os.path.join(root, r)).lower()])
    ci_found = [r for r in rel_files if any(r.startswith(p) or r == p for p in CI_PATHS)]
    locks = [r for r in rel_files if os.path.basename(r).lower() in
             {l.lower() for l in LOCKFILE_NAMES}]
    req_locked = 0
    req_total = 0
    for r in rel_files:
        if os.path.basename(r).lower().startswith("requirements"):
            for line in safe_read(os.path.join(root, r)).splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "-")):
                    req_total += 1
                    if "==" in line or "~=" in line:
                        req_locked += 1
    docker = [r for r in rel_files if os.path.basename(r) in
              ("Dockerfile", "docker-compose.yml", "docker-compose.yaml")]
    docs = {
        "readme": sorted(r for r in rel_files if re.match(r"(?i)readme", os.path.basename(r))),
        "changelog": sorted(r for r in rel_files
                            if re.match(r"(?i)changelog", os.path.basename(r))),
        "docs_dir": sorted(d for d in dir_names if d.lower().endswith("/docs") or d == "docs"),
    }
    env_files = [r for r in rel_files if re.match(r"(?i)^\.env", os.path.basename(r))]
    eval_assets = [r for r in rel_files if re.search(
        r"(?i)(eval|benchmark|评测|题库)", r) and not r.startswith(".git")]
    acceptance_md = [r for r in rel_files if r.upper().endswith("ACCEPTANCE.MD")]

    git = check_git(root)

    red_flags = []
    if git and git.get("env_tracked_by_git"):
        red_flags.append({"flag": "env_tracked", "severity": "P0",
                          "detail": ".env 被 git 跟踪，密钥可能已入库"})
    if git and git.get("env_file_exists") and not git.get("env_gitignored"):
        red_flags.append({"flag": "env_not_ignored", "severity": "P0",
                          "detail": ".env 存在但未被 gitignore"})
    if not locks and req_total > 0 and req_locked == 0:
        red_flags.append({"flag": "no_lockfile", "severity": "P1",
                          "detail": "无任何锁文件且依赖未版本锁定（不可复现构建）"})
    elif 0 < req_locked < req_total:
        red_flags.append({"flag": "requirements_not_fully_pinned", "severity": "P2",
                          "detail": "requirements 仅 %d/%d 个依赖锁定版本（>= 不是锁定）"
                                    % (req_locked, req_total)})
    if not test_files and not tests_dirs and not test_cfg:
        red_flags.append({"flag": "no_tests", "severity": "P1",
                          "detail": "未发现任何测试文件/目录/配置"})
    if not ci_found:
        red_flags.append({"flag": "no_ci", "severity": "P2", "detail": "未发现 CI 配置"})
    if not docs["readme"]:
        red_flags.append({"flag": "no_readme", "severity": "P2", "detail": "无 README"})
    if not docs["changelog"]:
        red_flags.append({"flag": "no_changelog", "severity": "P2",
                          "detail": "无 CHANGELOG（资产型产出按用户铁律 6 需补建）"})
    if suspected:
        red_flags.append({"flag": "suspected_secrets", "severity": "P0",
                          "detail": "发现 %d 处疑似硬编码凭据（初筛，已脱敏），需人工逐条确认；"
                                    "覆盖面：配置文件 %d 个 / 特定文件名 %d 个 已进扫描"
                                    % (cred_st["findings_total"],
                                       cred_st["by_channel"].get("config", 0),
                                       cred_st["by_channel"].get("special-name", 0))})
    if env_files and not any("example" in e.lower() for e in env_files):
        red_flags.append({"flag": "no_env_example", "severity": "P2",
                          "detail": "有 .env 但缺 .env.example 模板"})
    if coverage["insufficient"]:
        red_flags.append({"flag": "scan_coverage_insufficient", "severity": "P1",
                          "detail": coverage["declaration"],
                          "limits": [l["kind"] for l in coverage["limits_triggered"]]})

    profile = {
        "root": os.path.abspath(root),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git": git,
        "languages": dict(langs.most_common()),
        "manifests": [r for r in rel_files if os.path.basename(r) in MANIFEST_NAMES],
        "frameworks": {k: v for k, v in sorted(frameworks.items())},
        "type_guesses": guess_types(frameworks, hits),
        "tooling": {
            "tests": {"dirs": tests_dirs, "config": test_cfg, "file_count": len(test_files)},
            "lint": lint_found,
            "ci": ci_found,
            "lockfiles": locks,
            "requirements_locked": "%d/%d" % (req_locked, req_total),
            "docker": docker,
            "docs": docs,
            "env_files": env_files,
        },
        "eval_assets": eval_assets[:30],
        "acceptance_md": acceptance_md,
        "quick_counts": {
            "files_total": walk_st["files_collected"],
            "files_seen": walk_st["files_seen"],
            "language_scanned": lang_st["scanned"],
            "credential_scanned": cred_st["scanned"],
            "credential_in_scope": cred_st["files_in_scope"],
            "print_py_prod": len(hits["print_py_prod"]),
            "print_py_prod_sample": hits["print_py_prod"],
            "console_log_js": hits["console_log"],
            "bare_except_py": len(hits["bare_except"]),
            "bare_except_sample": hits["bare_except"],
            "todo_fixme": hits["todo"],
        },
        "credential_scan": cred_st,
        "suspected_secrets": suspected,
        "coverage": coverage,
        "red_flags": red_flags,
        "notes": [
            "本画像为初筛结果：疑似凭据与红旗必须人工复核后才能写进验收报告；"
            "凭据命中已在生成时脱敏，报告不得回填明文。",
            "自带正则只是初筛，覆盖面有限；建议补跑成熟组件（gitleaks / detect-secrets / "
            "TruffleHog）作交叉印证，两者结论不一致时以更严者为准。",
            "coverage.insufficient=true 时：禁止把「没有发现」写成「扫描通过」，"
            "必须把 limits_triggered/未检查清单抄进报告第 6 节。",
            "已知盲区三条，写结论前逐条自问：① 值形态闸会把「含中文的赋值值」判为自然语言并"
            "只计数不上报（见 credential_scan.shape_rejected）——若密钥混在中文句子里且不带"
            "sk-/ghp_/AKIA 这类格式特征，本初筛会漏，必须靠成熟组件补扫；"
            "② 源码信道的无引号赋值不启用（Python/JS 里等号右侧不带引号的是变量引用不是密钥字面量）；"
            "③ .git、node_modules 等名单目录整目录不进入，几乎必然出现在未检查清单里，"
            "这是覆盖声明的常态而非缺陷，历史提交里的泄露不在本脚本射程内。",
            "type_guesses 仅为启发式猜测，验收官应结合入口文件与 README 人工确认。",
        ],
    }
    return profile


def main():
    parser = argparse.ArgumentParser(description="项目画像：验收 Phase 0 初筛")
    parser.add_argument("root", help="项目根目录")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                        help="目录深度上限（默认 8 层）")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES,
                        help="遍历文件数上限（默认 20000）")
    parser.add_argument("--max-language-files", dest="max_language_files", type=int,
                        default=DEFAULT_MAX_LANGUAGE_FILES, help="语言分析通道文件数上限")
    parser.add_argument("--max-credential-files", dest="max_credential_files", type=int,
                        default=DEFAULT_MAX_CREDENTIAL_FILES, help="凭据扫描通道文件数上限")
    parser.add_argument("--max-read-bytes", dest="max_read_bytes", type=int,
                        default=DEFAULT_MAX_READ_BYTES,
                        help="单文件读取上限（字节，默认 262144）；超过即计入未检查清单")
    parser.add_argument("--max-findings", dest="max_findings", type=int, default=FINDINGS_CAP,
                        help="疑似凭据最多上报条数（默认 40）；超出部分按数量登记并置 truncated")
    parser.add_argument("--strict-coverage", action="store_true",
                        help="覆盖范围不足时退出码 3（便于门禁接住）")
    args = parser.parse_args()
    root = args.root
    if not os.path.isdir(root):
        print(json.dumps({"error": "目录不存在: %s" % root}, ensure_ascii=False))
        sys.exit(2)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    profile = build_profile(root, args)
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    if args.strict_coverage and profile["coverage"]["insufficient"]:
        sys.stderr.write("⚠️ 覆盖范围不足（详见 coverage.limits_triggered）\n")
        sys.exit(3)
    sys.exit(0)


if __name__ == "__main__":
    main()
