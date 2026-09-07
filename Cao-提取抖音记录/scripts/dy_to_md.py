# -*- coding: utf-8 -*-
"""读抖音 chat.db，按天导出 Markdown（清洗规则与微信一致）。"""
import os
import re
import sys
import json
import sqlite3
import datetime

WORK = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "db_path": r"D:\AI提炼\抖音聊天记录导出\data\chat.db",
    "output_dir": r"E:\Acht\记录\outputs\抖音聊天记录",
    "owner_uid": "",        # 留空自动从 conv_id 解析
    "self_name": "B",
    "peer_name": "A",
}


def load_cfg():
    cfg = dict(DEFAULTS)
    p = os.path.join(WORK, "config.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def clean(t):
    if not t:
        return ""
    t = re.sub(r"#\S+", "", t)      # 去 #话题
    t = re.sub(r"@\S+", "", t)      # 去 @提及
    t = re.sub(r"\s+", " ", t).strip()
    return t


def ts_text(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def ts_time(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%H:%M:%S")
    except Exception:
        return str(ts)


def _day(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def parse_cj(raw_data):
    if not raw_data:
        return {}
    try:
        m = json.loads(raw_data)
        cj = m.get("content_json", "")
        if isinstance(cj, str) and cj:
            cj = json.loads(cj)
        return cj if isinstance(cj, dict) else {}
    except Exception:
        return {}


def render_share(cj):
    at = str(cj.get("aweType", ""))
    if at == "800":
        title = clean(cj.get("content_title", ""))
        return ("[分享视频] " + title).strip() if title else "[分享视频]"
    if at == "10500":
        comment = clean(cj.get("comment", ""))
        return comment or "[分享评论]"
    t = clean(cj.get("push_detail") or cj.get("content_title") or cj.get("aweme_title") or "")
    return t or "[分享]"


def render_fwd(cj):
    title = clean(cj.get("title", ""))
    lc = cj.get("list_content", [])
    if isinstance(lc, str):
        try:
            lc = json.loads(lc)
        except Exception:
            lc = []
    lines = [("[转发聊天记录] " + title).strip() if title else "[转发聊天记录]"]
    for it in lc or []:
        if not isinstance(it, dict):
            continue
        nick = clean(it.get("nick_name", "")) or "?"
        txt = clean(it.get("text", "")) or ""
        lines.append("> %s：%s" % (nick, txt))
    return "\n".join(lines)


def render_content(msg_type, cj, voice_map, msg_id):
    # 语音
    if msg_type == 0 and cj.get("resource_url") and cj.get("duration") not in (None, "", "0"):
        try:
            dur = round(float(cj.get("duration")) / 1000)
        except (TypeError, ValueError):
            dur = 0
        label = ("[语音 %d秒]" % dur) if dur else "[语音]"
        vt = (voice_map or {}).get(str(msg_id), "")
        return (label + " " + vt).strip() if vt else label
    if msg_type == 2:
        dn = (cj.get("display_name") or "").strip()
        return "[%s]" % dn if dn else "[表情]"
    if msg_type == 3:
        return "[图片]"
    if msg_type == 5 or (isinstance(cj.get("video"), dict) and cj.get("video", {}).get("vid")):
        try:
            dur = round(float(cj.get("duration") or 0))
        except (TypeError, ValueError):
            dur = 0
        return ("[视频 %d秒]" % dur) if dur else "[视频]"
    if msg_type == 4:
        return render_share(cj)
    if msg_type == 0:
        if cj.get("list_content"):
            return render_fwd(cj)
        return None  # 系统消息跳过
    return clean(cj.get("text") or cj.get("description") or "")


def render_ref_content(cj):
    at = str(cj.get("aweType", ""))
    if at in ("500", "501", "507", "508", "510", "514", "516"):
        dn = (cj.get("display_name") or "").strip()
        return "[%s]" % dn if dn else "[表情]"
    if at in ("2702", "2703", "2704"):
        return "[图片]"
    if cj.get("resource_url") and cj.get("duration") not in (None, "", "0"):
        try:
            dur = round(float(cj.get("duration")) / 1000)
        except (TypeError, ValueError):
            dur = 0
        return ("[语音 %d秒]" % dur) if dur else "[语音]"
    if isinstance(cj.get("video"), dict) and cj.get("video", {}).get("vid"):
        return "[视频]"
    if at == "800":
        title = clean(cj.get("content_title", ""))
        return "[分享视频] " + title if title else "[分享视频]"
    return clean(cj.get("text") or cj.get("description") or "")


def render_ref(ref_str, id2sender, owner, self_name, peer_name):
    if not ref_str:
        return ""
    try:
        ref = json.loads(ref_str)
    except Exception:
        return ""
    sid = str(ref.get("server_id", ""))
    sender = id2sender.get("srv_" + sid)
    who = self_name if (sender and sender == owner) else peer_name
    txt = clean(ref.get("content") or "")
    if not txt:
        rc = ref.get("refmsg_content", "")
        try:
            rcj = json.loads(rc) if isinstance(rc, str) and rc else {}
        except Exception:
            rcj = {}
        txt = render_ref_content(rcj if isinstance(rcj, dict) else {})
    if not txt:
        txt = "[消息]"
    return "%s：%s" % (who, txt)


def detect_owner(conn, cfg_owner):
    if cfg_owner:
        return str(cfg_owner)
    row = conn.execute("SELECT conv_id FROM conversations LIMIT 1").fetchone()
    if row:
        parts = str(row[0]).split(":")
        if len(parts) >= 4:
            return parts[2]
    return ""


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_cfg()
    db = cfg["db_path"]
    out_dir = cfg["output_dir"]
    self_name = cfg["self_name"]
    peer_name = cfg["peer_name"]

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    owner = detect_owner(conn, cfg["owner_uid"])

    # 语音转写（表可能不存在）
    voice_map = {}
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voice_transcriptions'").fetchone():
        for r in conn.execute("SELECT msg_id, text_result FROM voice_transcriptions WHERE status='success'"):
            voice_map[str(r["msg_id"])] = r["text_result"]

    id2sender = {}
    for r in conn.execute("SELECT msg_id, sender_uid FROM messages"):
        id2sender[str(r["msg_id"])] = r["sender_uid"]

    conv = conn.execute("SELECT conv_id, name FROM conversations LIMIT 1").fetchone()
    conv_name = conv["name"] if conv else "会话"
    peer_uid = ""
    peer_nick = conv_name
    for r in conn.execute("SELECT DISTINCT sender_uid FROM messages WHERE sender_uid != ?", (owner,)):
        peer_uid = str(r["sender_uid"])
        break
    if peer_uid:
        u = conn.execute("SELECT nickname FROM users WHERE uid = ?", (peer_uid,)).fetchone()
        if u and u["nickname"]:
            peer_nick = u["nickname"]

    rows = conn.execute("SELECT * FROM messages ORDER BY timestamp, seq").fetchall()
    conn.close()

    # 按天分组
    days = {}
    for r in rows:
        days.setdefault(_day(r["timestamp"]), []).append(r)

    os.makedirs(out_dir, exist_ok=True)
    folder = os.path.join(out_dir, "抖音聊天记录_与" + peer_name)
    os.makedirs(folder, exist_ok=True)

    state_path = os.path.join(folder, "_export_state.json")
    state = {}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path, encoding="utf-8"))
        except Exception:
            state = {}

    day_counts = {}
    for day in sorted(days):
        day_rows = days[day]
        day_rows.sort(key=lambda r: (r["timestamp"] or 0, r["seq"] or 0))
        day_counts[day] = len(day_rows)
        max_time = max((r["timestamp"] or 0) for r in day_rows)
        last_id = str(day_rows[-1]["msg_id"])
        current = {"count": len(day_rows), "max_time": max_time, "last_id": last_id}
        day_path = os.path.join(folder, day + "dy.md")
        if state.get(day) == current and os.path.exists(day_path):
            continue
        lines = ["# 与 %s 的聊天记录（%s）" % (peer_name, day), "",
                 "- 对方：%s（昵称 %s）" % (peer_name, peer_nick),
                 "- 日期：%s" % day, "- 消息数：%d" % len(day_rows), "", "---", ""]
        first = True
        for r in day_rows:
            sender = r["sender_uid"]
            who = self_name if (sender and str(sender) == owner) else peer_name
            cj = parse_cj(r["raw_data"])
            md = render_content(r["msg_type"], cj, voice_map, r["msg_id"])
            if md is None:
                continue
            ref = render_ref(r["ref_msg"], id2sender, owner, self_name, peer_name)
            if ref:
                md = "引用【%s】 %s" % (ref, md)
            if not md.startswith("[转发聊天记录]"):
                md = md.replace("\n", " ").strip()
            if not md:
                md = "[无内容]"
            ts = ts_text(r["timestamp"]) if first else ts_time(r["timestamp"])
            lines.append("**%s** （%s）：%s" % (who, ts, md))
            lines.append("")
            first = False
        with open(day_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        state[day] = current
        print("Wrote", day_path, "(%d msgs)" % len(day_rows))

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    idx_lines = ["# 与 %s 的聊天记录（按天）" % peer_name, "",
                 "更新时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""]
    for day in sorted(day_counts):
        idx_lines.append("- [%s](%sdy.md)：%d 条消息" % (day, day, day_counts[day]))
    with open(os.path.join(folder, "索引.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(idx_lines) + "\n")
    print("Wrote", os.path.join(folder, "索引.md"))


if __name__ == "__main__":
    main()
