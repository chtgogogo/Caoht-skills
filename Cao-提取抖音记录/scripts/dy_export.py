# -*- coding: utf-8 -*-
"""一键导出抖音私信：增量采集 → 语音转写 → 清洗 → 按天 Markdown → 打开文件夹。"""
import os
import sys
import json
import subprocess
import traceback

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)

APP_DIR = r"D:\AI提炼\抖音聊天记录导出\app"
PY = os.path.join(APP_DIR, ".venv", "Scripts", "python.exe")


def load_filter():
    try:
        with open(os.path.join(WORK, "config.json"), encoding="utf-8") as f:
            return (json.load(f).get("filter") or "").strip()
    except Exception:
        return ""


def _env():
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = r"D:\AI提炼\models\playwright"
    return env


def logged_in():
    profile = os.path.join(r"D:\AI提炼\抖音聊天记录导出", "data", "browser_profile")
    if not os.path.isdir(profile):
        return False
    for _root, _dirs, files in os.walk(profile):
        if files:
            return True
    return False


def collect_dom(filter_name):
    """用 DOM 方式采集指定会话的最新消息（绕开抖音 secsdk 接口限制）。"""
    if not os.path.exists(PY):
        print("未找到本地运行环境，跳过采集。")
        return
    print("正在用 DOM 方式采集最新消息（只采：%s）..." % (filter_name or "全部"))
    script = os.path.join(WORK, "dy_dom_collect.py")
    cmd = [PY, script]
    try:
        r = subprocess.run(
            cmd, cwd=WORK, env=_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900,
        )
        print(r.stdout[-1200:] if r.stdout else "")
        if r.returncode != 0:
            print("采集未完成：", r.stderr[-500:])
    except Exception as e:
        print("采集失败（跳过）：", e)


def transcribe_voices():
    """用抖音官方接口把历史语音转文字（需要登录态）。"""
    if not os.path.exists(PY):
        print("未找到本地运行环境，跳过语音转写。")
        return
    print("正在补充语音转文字（如无新语音会很快结束）...")
    try:
        r = subprocess.run(
            [PY, "extract.py", "--transcribe-voices"],
            cwd=APP_DIR, env=_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,
        )
        print(r.stdout[-1500:] if r.stdout else "")
        if r.returncode != 0:
            print("语音转写未完成（可能登录已过期，不影响文字导出）：", r.stderr[-500:])
    except Exception as e:
        print("语音转写失败（跳过）：", e)


def open_folder():
    out = r"E:\Acht\记录\outputs\抖音聊天记录"
    try:
        target = out
        for name in sorted(os.listdir(out)):
            p = os.path.join(out, name)
            if os.path.isdir(p) and name.startswith("抖音聊天记录_与"):
                target = p
                break
        os.startfile(target)
    except Exception as e:
        print("打开文件夹失败：", e)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    filter_name = load_filter()
    if logged_in():
        collect_dom(filter_name)
        transcribe_voices()
    else:
        print("未检测到登录态，跳过采集和语音转写（已采到的消息照常导出）。")
    print("\n== 清洗并导出 Markdown ==")
    import dy_to_md
    dy_to_md.main()
    print("\n完成！")
    open_folder()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
