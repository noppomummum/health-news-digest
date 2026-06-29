"""
Daily Thai health & illness awareness digest.
Picks the 3 most relevant Thai articles, summarises them in Thai, and emails them.
"""
import os
import re
import time
import smtplib
import traceback
from email.message import EmailMessage

import feedparser
from google import genai
from google.genai import types
from google.genai import errors as genai_errors


# ---------- Configuration ----------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")  or ""
APP_PASSWORD   = os.environ.get("APP_PASSWORD")   or ""

FEED_URL = (
    "https://news.google.com/rss/search?q="
    "%E0%B8%AA%E0%B8%B8%E0%B8%82%E0%B8%A0%E0%B8%B2%E0%B8%9E+OR+"
    "%E0%B9%82%E0%B8%A3%E0%B8%84+OR+"
    "%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B8%81%E0%B8%B1%E0%B8%99%E0%B8%AA%E0%B8%B8%E0%B8%82%E0%B8%A0%E0%B8%B2%E0%B8%9E+OR+"
    "%E0%B9%82%E0%B8%A3%E0%B8%87%E0%B8%9E%E0%B8%A2%E0%B8%B2%E0%B8%9A%E0%B8%B2%E0%B8%A5"
    "&hl=th&gl=TH&ceid=TH:th"
)
SELECT_MODEL = "gemini-2.5-flash-lite"   # simple job, lite is fine
SUMMARY_MODEL = "gemini-2.5-flash"        # harder job, full flash handles Thai better
NUM_PICKS = 3


# ---------- AI helper with retry (no URL context anymore) ----------
def call_ai(client, model, prompt):
    config = types.GenerateContentConfig(max_output_tokens=8000)
    for attempt in range(5):
        wait = 5 * (attempt + 1)
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt, config=config,
            )
            if resp.text and resp.text.strip():
                return resp
            print(f"Empty reply on attempt {attempt + 1}; waiting {wait}s.")
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            print(f"Model error on attempt {attempt + 1} ({type(e).__name__}); waiting {wait}s.")
        time.sleep(wait)
    raise RuntimeError("Gemini didn't return a usable reply after 5 attempts.")


# ---------- Email helper ----------
def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, APP_PASSWORD)
        server.send_message(msg)


# ---------- The main job ----------
def build_and_send_digest():
    feed = feedparser.parse(FEED_URL)
    articles = feed.entries[:15]
    if not articles:
        raise RuntimeError("Feed returned 0 articles.")

    headline_list = "".join(f"{i}. {a.title}\n" for i, a in enumerate(articles, start=1))

    client = genai.Client(api_key=GEMINI_API_KEY)

    # First AI call: pick the top 3
    selection_prompt = (
        "ต่อไปนี้คือรายการพาดหัวข่าว:\n\n"
        + headline_list
        + f"\nเลือกพาดหัวข่าว {NUM_PICKS} ข่าวที่เกี่ยวข้องกับสุขภาพ โรค "
        "และการสร้างความตระหนักด้านสุขภาพมากที่สุด เรียงจากสำคัญที่สุดก่อน "
        f"ตอบกลับเป็นตัวเลข {NUM_PICKS} ตัวคั่นด้วยจุลภาคเท่านั้น เช่น: 4, 11, 2"
    )
    selection_response = call_ai(client, SELECT_MODEL, selection_prompt)
    choices = [int(n) for n in re.findall(r"\d+", selection_response.text)][:NUM_PICKS]
    chosen = [articles[c - 1] for c in choices]

    # Build the article block from headline + snippet (no URL fetching)
    article_block = ""
    for rank, a in enumerate(chosen, start=1):
        snippet = getattr(a, "summary", "")
        snippet = re.sub(r"<[^>]+>", "", snippet)  # strip any HTML tags from snippet
        article_block += f"ข่าวที่ {rank}:\nพาดหัว: {a.title}\nเนื้อหาย่อ: {snippet}\n\n"

    # Second AI call: Thai summaries from headline + snippet
    summary_prompt = (
        "คุณกำลังเขียนสรุปข่าวสุขภาพประจำวันให้คนทั่วไปอ่าน เพื่อสร้างความตระหนักด้านสุขภาพ\n"
        "สำหรับข่าวทั้งสามข่าวด้านล่าง ให้เขียนสรุปเป็นภาษาไทยที่อ่านง่าย น่าสนใจ ชวนอ่าน "
        "ความยาวไม่เกิน 200 คำต่อข่าว โดยอ้างอิงจากพาดหัวและเนื้อหาย่อที่ให้มา\n\n"
        "แต่ละข่าวให้เขียนตามนี้:\n"
        "  - บรรทัดแรก: พาดหัวสั้น ๆ ที่ดึงดูดความสนใจ ขึ้นต้นด้วยอิโมจิที่เหมาะสม 1 ตัว\n"
        "  - บรรทัดถัดมา 'ทำไมถึงสำคัญ:' ตามด้วยประโยคเดียวที่บอกว่าทำไมผู้อ่านควรสนใจ\n"
        "  - ย่อหน้าสรุป: อธิบายว่าข่าวเกี่ยวกับอะไร และมีความหมายอย่างไรต่อสุขภาพของคนทั่วไป "
        "ด้วยภาษาที่เข้าใจง่าย\n\n"
        "ใช้ภาษาที่เป็นมิตร ชวนอ่าน คงความถูกต้องและเป็นกลาง ไม่ให้คำแนะนำทางการแพทย์ส่วนบุคคล\n\n"
        + article_block
        + "ตอบกลับตามรูปแบบนี้เป๊ะ ๆ ห้ามใส่ JSON ห้ามใส่ code fence:\n\n"
        "=== SUMMARY 1 ===\n<สรุปข่าวที่ 1>\n\n"
        "=== SUMMARY 2 ===\n<สรุปข่าวที่ 2>\n\n"
        "=== SUMMARY 3 ===\n<สรุปข่าวที่ 3>"
    )
    summary_response = call_ai(client, SUMMARY_MODEL, summary_prompt)

    # Split tolerantly
    raw = summary_response.text.strip()
    parts = re.split(r"(?:===\s*)?SUMMARY\s*\d+(?:\s*===)?", raw)
    summaries = [p.strip() for p in parts[1:] if p.strip()]
    if not summaries:
        summaries = [raw]

    # Compose a friendly, readable email
    lines = [
        "🌅 สวัสดีตอนเช้า! นี่คือสรุปข่าวสุขภาพประจำวันของคุณ",
        "อ่านสั้น ๆ วันละ 3 ข่าว เพื่อสุขภาพที่ดีขึ้น 💪",
        "",
        "═" * 30,
        "",
    ]
    for rank, a in enumerate(chosen, start=1):
        text = summaries[rank - 1] if rank - 1 < len(summaries) else "(ไม่มีสรุป)"
        lines.append(f"【 ข่าวที่ {rank} 】")
        lines.append("")
        lines.append(text)
        lines.append("")
        lines.append(f"🔗 อ่านต่อ: {a.link}")
        lines.append("")
        lines.append("─" * 30)
        lines.append("")
    lines.append("ดูแลสุขภาพด้วยนะครับ/ค่ะ 🍀")
    body = "\n".join(lines)

    send_email("🌅 สรุปข่าวสุขภาพประจำวัน", body)
    print("Digest sent.")


# ---------- Entry point with safety net ----------
if __name__ == "__main__":
    try:
        build_and_send_digest()
    except Exception:
        error_details = traceback.format_exc()
        print("Run failed:\n" + error_details)
        try:
            send_email(
                "Daily digest FAILED",
                "Today's health digest didn't run successfully.\n\n"
                "Error details:\n\n" + error_details,
            )
        except Exception:
            print("Could not send failure email either.")
            raise
