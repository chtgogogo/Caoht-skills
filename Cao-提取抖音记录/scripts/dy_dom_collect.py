import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime

from playwright.async_api import async_playwright

PROFILE = r"D:\AI提炼\抖音聊天记录导出\data\browser_profile"
CHAT_URL = "https://www.douyin.com/chat?isPopup=1"
DB_PATH = r"D:\AI提炼\抖音聊天记录导出\data\chat.db"
TARGET = "想要天天欺负的天使可爱妹妹"

SEL_MSG_BOX = 'div[class*="messageMessageBoxmessageBox"]'
SEL_MSG_LIST = 'div[class*="messageMessageListlist"]'


def load_target():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return (json.load(f).get("filter") or TARGET).strip()
    except Exception:
        return TARGET

EMOJI_AWE = {500, 501, 507, 508, 510, 514, 516, 703}
IMAGE_AWE = {2702, 2703, 2704}


def ts_from_server_id(server_id):
    try:
        return int(int(server_id) >> 32)
    except Exception:
        return 0


def classify(parsed):
    if not isinstance(parsed, dict):
        parsed = {}
    at = parsed.get("aweType") or parsed.get("awemeType") or 0
    try:
        at = int(at)
    except Exception:
        at = 0
    text = str(parsed.get("text") or parsed.get("description") or "").strip()
    resource = parsed.get("resource_url")
    duration = parsed.get("duration")
    video = parsed.get("video") if isinstance(parsed.get("video"), dict) else {}

    if isinstance(resource, dict) and duration not in (None, "", 0, "0"):
        try:
            dur = round(float(duration) / 1000)
        except Exception:
            dur = 0
        return 0, ("[语音 %d秒]" % dur) if dur else "[语音]"
    if at in EMOJI_AWE:
        dn = str(parsed.get("display_name") or "").strip()
        return 2, ("[%s]" % dn) if dn else "[表情]"
    if at in IMAGE_AWE:
        return 3, "[图片]"
    if video.get("vid") or at in (701,):
        try:
            dur = round(float(duration or 0))
        except Exception:
            dur = 0
        return 5, ("[视频 %d秒]" % dur) if dur else "[视频]"
    if at in (68, 800, 801, 803, 10500, 11054, 11055, 11063, 11066, 11067, 11069, 11070, 11029, 10401):
        return 4, text or "[分享]"
    if text:
        return 1, text
    return 0, ""


def ensure_conversation(conn, conv_id, name, owner_uid, peer_uid):
    conn.execute(
        """INSERT INTO conversations (conv_id, conv_type, name, participant_uids)
           VALUES (?, 1, ?, ?)
           ON CONFLICT(conv_id) DO UPDATE SET name=COALESCE(excluded.name, name)""",
        (conv_id, name, json.dumps([owner_uid, peer_uid], ensure_ascii=False)),
    )
    conn.commit()


def insert_messages(conn, items, conv_id, owner_uid, peer_uid, existing_sids):
    inserted = 0
    for it in items:
        sid = str(it.get("serverId") or "")
        if not sid or sid in existing_sids:
            continue
        msg_id = "dom_" + sid
        parsed = it.get("parsedContent") or {}
        msg_type, content = classify(parsed)
        if not content:
            content = str(it.get("content") or "").strip()
        if not content:
            content = "[无内容]"
        is_self = bool(it.get("domIsSelf"))
        sender_uid = owner_uid if is_self else peer_uid
        sender_name = "__self__" if is_self else ""
        timestamp = ts_from_server_id(sid)
        raw = {
            "server_id": sid,
            "content_json": json.dumps(parsed, ensure_ascii=False),
            "type": it.get("type"),
            "is_self": is_self,
        }
        ref = it.get("ref")
        ref_json = None
        if isinstance(ref, dict) and ref.get("serverId"):
            ref_json = json.dumps({
                "server_id": str(ref.get("serverId")),
                "content": ref.get("content") or "",
                "refmsg_content": json.dumps(ref.get("parsedContent") or {}, ensure_ascii=False),
                "nickname": ref.get("nickName") or "",
            }, ensure_ascii=False)
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
               (msg_id, conv_id, sender_uid, sender_name, content, msg_type,
                media_url, media_local_path, timestamp, seq, raw_data, ref_msg)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)""",
            (msg_id, conv_id, sender_uid, sender_name, content, msg_type,
             timestamp, timestamp, json.dumps(raw, ensure_ascii=False), ref_json),
        )
        if cur.rowcount:
            inserted += 1
    conn.execute(
        """UPDATE conversations SET
             message_count = (SELECT COUNT(*) FROM messages WHERE conv_id = ?),
             last_message_time = (SELECT MAX(timestamp) FROM messages WHERE conv_id = ?)
           WHERE conv_id = ?""",
        (conv_id, conv_id, conv_id),
    )
    conn.commit()
    return inserted


async def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1400, "height": 900},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(9000)
            try:
                body = await page.evaluate("document.body ? document.body.innerText : ''")
                if "是否保存登录信息" in body:
                    btn = page.get_by_text("保存", exact=True)
                    if await btn.count():
                        await btn.first.click()
                        await page.wait_for_timeout(4000)
            except Exception:
                pass
            target = load_target()
            await page.get_by_text(target, exact=False).first.click(timeout=8000)
            await page.wait_for_timeout(5000)

            conv_id = await page.evaluate("window.conversationStore && window.conversationStore.curConversationId || ''")
            owner_uid = await page.evaluate("window.userInfoStore && window.userInfoStore.curLoginUserInfo && window.userInfoStore.curLoginUserInfo.uid || ''")
            owner_uid = str(owner_uid)
            parts = str(conv_id).split(":")
            peer_uid = parts[3] if len(parts) >= 4 else ""
            print("conv_id", conv_id, "owner", owner_uid, "peer", peer_uid)

            # 滚到顶部，然后向下逐页加载并收集
            await page.evaluate(
                """(sel) => {
                    let el = document.querySelector(sel);
                    while (el && el.scrollHeight <= el.clientHeight && el.parentElement) el = el.parentElement;
                    if (el) el.scrollTop = 0;
                }""",
                SEL_MSG_LIST,
            )
            await page.wait_for_timeout(1200)

            seen = {}
            stable = 0
            for _ in range(300):
                boxes = await page.evaluate(
                    """(sel) => {
                        const boxes = [...document.querySelectorAll(sel)];
                        const out = [];
                        for (const el of boxes) {
                            const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
                            if (!key) continue;
                            let current = null;
                            let ref = null;
                            function walk(f, isRoot) {
                                if (!f || (current && ref)) return;
                                const p = f.memoizedProps;
                                if (p && typeof p === 'object') {
                                    for (const name of ['message', 'refMessage']) {
                                        const v = p[name];
                                        if (!v || typeof v !== 'object') continue;
                                        if (name === 'message' && v.serverId && !current) current = v;
                                        if (name === 'refMessage' && v.serverId && !ref) ref = v;
                                    }
                                }
                                if (f.child) walk(f.child, false);
                                if (!isRoot && f.sibling) walk(f.sibling, false);
                            }
                            walk(el[key], true);
                            if (current && current.serverId) {
                                out.push({
                                    serverId: String(current.serverId),
                                    type: current.type,
                                    isMyMessage: current.isMyMessage,
                                    isFromMe: current.isFromMe,
                                    domIsSelf: !!(el.querySelector('[class*="isFromMe"]') || String(el.className).includes('isFromMe')),
                                    parsedContent: current.parsedContent || null,
                                    content: current.content || null,
                                    ref: ref ? {
                                        serverId: String(ref.serverId),
                                        content: ref.content,
                                        parsedContent: ref.parsedContent,
                                        nickName: ref.nickName,
                                    } : null,
                                });
                            }
                        }
                        return out;
                    }""",
                    SEL_MSG_BOX,
                )
                added = 0
                for b in boxes:
                    sid = b.get("serverId")
                    if sid and sid not in seen:
                        seen[sid] = b
                        added += 1
                before = await page.evaluate(
                    """(sel) => {
                        let el = document.querySelector(sel);
                        while (el && el.scrollHeight <= el.clientHeight && el.parentElement) el = el.parentElement;
                        if (!el) return 0;
                        return el.scrollTop;
                    }""",
                    SEL_MSG_LIST,
                )
                moved = await page.evaluate(
                    """(sel) => {
                        let el = document.querySelector(sel);
                        while (el && el.scrollHeight <= el.clientHeight && el.parentElement) el = el.parentElement;
                        if (!el) return false;
                        const old = el.scrollTop;
                        el.scrollTop = old + 700;
                        return el.scrollTop !== old;
                    }""",
                    SEL_MSG_LIST,
                )
                await page.wait_for_timeout(350)
                if added:
                    stable = 0
                else:
                    stable += 1
                if not moved and stable >= 2:
                    break

            print("collected", len(seen))
            items = sorted(seen.values(), key=lambda x: ts_from_server_id(x.get("serverId", "0")))
            for it in items[-10:]:
                sid = it.get("serverId")
                t = ts_from_server_id(sid)
                print(datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"), it.get("isMyMessage"), (it.get("parsedContent") or {}).get("text", "")[:60])

            if conv_id:
                conn = sqlite3.connect(DB_PATH)
                try:
                    conn.execute("DELETE FROM messages WHERE msg_id LIKE 'dom_%' AND conv_id=?", (conv_id,))
                    existing_sids = set()
                    for (raw,) in conn.execute(
                        "SELECT raw_data FROM messages WHERE conv_id=? AND raw_data IS NOT NULL",
                        (conv_id,),
                    ):
                        try:
                            j = json.loads(raw)
                            if j.get("server_id"):
                                existing_sids.add(str(j["server_id"]))
                        except Exception:
                            pass
                    ensure_conversation(conn, conv_id, target, owner_uid, peer_uid)
                    inserted = insert_messages(conn, items, conv_id, owner_uid, peer_uid, existing_sids)
                    print("inserted", inserted)
                finally:
                    conn.close()
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
