#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目画像脚本 —— 项目验收官 skill 的 Phase 0 工具。

用法:
    python profile_project.py <项目根目录>

输出: 一份 JSON 画像(语言/框架/项目类型猜测/工具链/红旗/疑似密钥), 供验收官决定
     哪些检查项适用、动态启发从哪里下手。纯标准库, 无任何第三方依赖。
说明: 本脚本是"快筛"不是"终判"——疑似密钥等红旗必须人工逐条确认后才可写进报告。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import Counter

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".idea", ".vscode", "dist", "build", ".next", ".nuxt", "target",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", ".tox",
    "site-packages", ".gradle", "bin", "obj", "vendor",
}
SOURCE_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "typescript",
    ".go": "go", ".java": "java", ".rb": "ruby", ".php": "php",
    ".rs": "rust", ".c": "c", ".cpp": "c++", ".cs": "c#",
    ".sql": "sql", ".sh": "shell", ".ps1": "powershell", ".bat": "batch",
    ".gd": "gdscript", ".vue": "vue", ".svelte": "svelte",
}
SCAN_EXT = set(SOURCE_EXT) | {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".txt", ".md"}
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

SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("generic_assignment", re.compile(
        r"""(?i)(api[_-]?key|secret|token|passwd|password)\s*[=:]\s*["'][^"']{12,}["']""")),
]
PLACEHOLDER_WORDS = re.compile(
    r"(?i)(xxx|your[_-]?|example|placeholder|dummy|sample|test|changeme|todo|<[^>]+>)")

BARE_EXCEPT_RE = re.compile(r"except\s*(Exception)?\s*:")
PRINT_RE = re.compile(r"^\s*print\(")
CONSOLE_LOG_RE = re.compile(r"console\.log\(")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
CLI_PY_RE = re.compile(r"^\s*(import|from)\s+(argparse|click|typer)\b", re.M)


def safe_read(path, max_bytes=256 * 1024):
    try:
        if os.path.getsize(path) > max_bytes:
            return ""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def run_git(root, *args):
    try:
        out = subprocess.run(
            ["git", "-C", root] + list(args), capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="ignore")
        return out.returncode, (out.stdout or "").strip(), (out.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, "", "git unavailable"


def walk_files(root, max_files=20000):
    files = []
    for base, dirs, names in os.walk(root):
        rel_base = os.path.relpath(base, root)
        depth = 0 if rel_base == "." else rel_base.count(os.sep) + 1
        if depth > 8:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != ".git"]
        for n in names:
            files.append(os.path.join(base, n))
            if len(files) >= max_files:
                return files
    return files


def detect_frameworks(root, files):
    found = {}
    for fp in files:
        name = os.path.basename(fp).lower()
        if name not in {m.lower() for m in MANIFEST_NAMES}:
            continue
        content = safe_read(fp, 400 * 1024)
        low = content.lower()
        rel = os.path.relpath(fp, root).replace("\\", "/")
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
                    found.setdefault(fw, []).append(rel)
            if data.get("bin"):
                found.setdefault("js-bin-entry", []).append(rel)
        elif name in ("requirements.txt", "requirements-dev.txt",
                      "requirements_base.txt", "pipfile"):
            for fw in PY_FRAMEWORKS:
                if re.search(r"(?im)^\s*[-a-z0-9_.]*" + re.escape(fw) + r"\b", low):
                    found.setdefault(fw, []).append(rel)
        elif name in ("pyproject.toml", "setup.py"):
            for fw in PY_FRAMEWORKS:
                if re.search(r"(?i)[\"']" + re.escape(fw) + r"[\"']?", low):
                    found.setdefault(fw, []).append(rel)
            if name == "pyproject.toml" and re.search(r"\[project\.scripts\]", low):
                found.setdefault("python-console-scripts", []).append(rel)
        elif name == "go.mod":
            for fw in GO_FRAMEWORKS:
                if fw.split("/")[-1] in low or fw in low:
                    found.setdefault(fw, []).append(rel)
        elif name == "cargo.toml":
            for fw in RUST_FRAMEWORKS:
                if fw in low:
                    found.setdefault(fw, []).append(rel)
        elif name == "project.godot":
            found.setdefault("godot", []).append(rel)
    return found


def source_scan(root, files):
    langs, ext_file_count = Counter(), Counter()
    hits = {
        "cli_python": 0, "print_py_prod": [], "console_log": 0,
        "bare_except": [], "todo": 0,
    }
    suspected = []
    scanned = 0
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        if ext in SOURCE_EXT:
            ext_file_count[SOURCE_EXT[ext]] += 1
        if ext not in SCAN_EXT:
            continue
        rel = os.path.relpath(fp, root).replace("\\", "/")
        low_rel = rel.lower()
        parts = low_rel.split("/")
        is_test = any(p.startswith("test") or p in ("tests", "__tests__", "spec")
                      for p in parts) or bool(re.search(r"(test_|_test\.|\.test\.)", low_rel))
        content = safe_read(fp)
        if not content or ext not in SOURCE_EXT:
            continue
        scanned += 1
        if scanned > 3000:
            break
        if ext == ".py" and not is_test:
            if CLI_PY_RE.search(content):
                hits["cli_python"] += 1
            for i, line in enumerate(content.splitlines(), 1):
                if PRINT_RE.match(line):
                    hits["print_py_prod"].append(f"{rel}:{i}")
        if ext in (".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte") and not is_test:
            hits["console_log"] += len(CONSOLE_LOG_RE.findall(content))
        if ext == ".py":
            for i, line in enumerate(content.splitlines(), 1):
                if BARE_EXCEPT_RE.search(line):
                    hits["bare_except"].append(f"{rel}:{i}")
        hits["todo"] += len(TODO_RE.findall(content))
        if ext not in (".md", ".txt"):
            for i, line in enumerate(content.splitlines(), 1):
                for pat_name, pat in SECRET_PATTERNS:
                    m = pat.search(line)
                    if m and not PLACEHOLDER_WORDS.search(line):
                        suspected.append(
                            {"file": rel, "line": i, "pattern": pat_name,
                             "snippet": line.strip()[:120]})
                        break
    hits["print_py_prod"] = hits["print_py_prod"][:15]
    hits["bare_except"] = hits["bare_except"][:15]
    langs.update({k: v for k, v in ext_file_count.items()})
    return langs, hits, suspected[:20], scanned


def guess_types(frameworks, hits, dirs):
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


def build_profile(root):
    files = walk_files(root)
    basenames = {os.path.basename(f) for f in files}
    rel_files = [os.path.relpath(f, root).replace("\\", "/") for f in files]
    dir_names = {os.path.dirname(r) for r in rel_files}

    frameworks = detect_frameworks(root, files)
    langs, hits, suspected, scanned = source_scan(root, files)

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
                          "detail": f"requirements 仅 {req_locked}/{req_total} 个依赖锁定版本（>= 不是锁定）"})
    if not test_files and not tests_dirs and not test_cfg:
        red_flags.append({"flag": "no_tests", "severity": "P1",
                          "detail": "未发现任何测试文件/目录/配置"})
    if not ci_found:
        red_flags.append({"flag": "no_ci", "severity": "P2",
                          "detail": "未发现 CI 配置"})
    if not docs["readme"]:
        red_flags.append({"flag": "no_readme", "severity": "P2",
                          "detail": "无 README"})
    if not docs["changelog"]:
        red_flags.append({"flag": "no_changelog", "severity": "P2",
                          "detail": "无 CHANGELOG（资产型产出按用户铁律 6 需补建）"})
    if suspected:
        red_flags.append({"flag": "suspected_secrets", "severity": "P0",
                          "detail": f"发现 {len(suspected)} 处疑似硬编码密钥，需人工逐条确认"})
    if env_files and not any("example" in e.lower() for e in env_files):
        red_flags.append({"flag": "no_env_example", "severity": "P2",
                          "detail": "有 .env 但缺 .env.example 模板"})

    profile = {
        "root": os.path.abspath(root),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git": git,
        "languages": dict(langs.most_common()),
        "manifests": [r for r in rel_files if os.path.basename(r) in MANIFEST_NAMES],
        "frameworks": {k: v for k, v in sorted(frameworks.items())},
        "type_guesses": guess_types(frameworks, hits, dir_names),
        "tooling": {
            "tests": {"dirs": tests_dirs, "config": test_cfg,
                      "file_count": len(test_files)},
            "lint": lint_found,
            "ci": ci_found,
            "lockfiles": locks,
            "requirements_locked": f"{req_locked}/{req_total}",
            "docker": docker,
            "docs": docs,
            "env_files": env_files,
        },
        "eval_assets": eval_assets[:30],
        "acceptance_md": acceptance_md,
        "quick_counts": {
            "files_total": len(files),
            "source_scanned": scanned,
            "print_py_prod": len(hits["print_py_prod"]),
            "print_py_prod_sample": hits["print_py_prod"],
            "console_log_js": hits["console_log"],
            "bare_except_py": len(hits["bare_except"]),
            "bare_except_sample": hits["bare_except"],
            "todo_fixme": hits["todo"],
        },
        "suspected_secrets": suspected,
        "red_flags": red_flags,
        "notes": [
            "本画像为快筛结果：疑似密钥与红旗必须人工复核后才能写进验收报告。",
            "type_guesses 仅为启发式猜测，验收官应结合入口文件与 README 人工确认。",
        ],
    }
    return profile


def main():
    parser = argparse.ArgumentParser(description="项目画像：验收 Phase 0 快筛")
    parser.add_argument("root", help="项目根目录")
    args = parser.parse_args()
    root = args.root
    if not os.path.isdir(root):
        print(json.dumps({"error": f"目录不存在: {root}"}, ensure_ascii=False))
        sys.exit(2)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    profile = build_profile(root)
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
