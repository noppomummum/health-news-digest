"""
Daily health & illness awareness digest.
Picks the 3 most relevant articles from Google News and emails detailed summaries.
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
# When this runs on GitHub, real values come from GitHub Secrets via environment variables.
# When this runs on your laptop for testing, the fallback after "or" is used.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")  or ""
APP_PASSWORD   = os.environ.get("APP_PASSWORD")   or ""

FEED_URL = (
    "https://news.google.com/rss/search?q="
    "%22health+insurance%22+OR+%22critical+illness%22+OR+%22medical+insurance%22+OR+healthcare"
    "&hl=en&gl=TH&ceid=TH:en"
)
MODEL = "gemini-2.5-flash-lite"
NUM_PICKS = 3


# ---------- AI helper with retry ----------
def call_ai(client, prompt, use_url_context=False):
    config = None
    if use_url_context:
        config = types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())],
        )
    for attempt in range(5):
        wait = 5 * (attempt + 1)
        try:
            resp = client.models.generate_content(
                model=MODEL, contents=prompt, config=config,
            )
            if resp.text and resp.text.strip():
                return resp
            print(f"Empty response on attempt {attempt + 1}; waiting {wait}s.")
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
    # 1) Fetch news
    feed = feedparser.parse(FEED_URL)
    articles = feed.entries[:15]
    if not articles:
        raise RuntimeError("Feed returned 0 articles.")

    # 2) Numbered list for the selection prompt
    headline_list = "".join(f"{i}. {a.title}\n" for i, a in enumerate(articles, start=1))

    # 3) Connect
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 4) Pick the top 3
    selection_prompt = (
        "Here is a numbered list of news headlines:\n\n"
        + headline_list
        + f"\nPick the {NUM_PICKS} headlines most relevant to health insurance or "
        "critical illness insurance awareness, ranked best first. "
        f"Reply with only the {NUM_PICKS} numbers separated by commas, like: 4, 11, 2"
    )
    selection_response = call_ai(client, selection_prompt)
    choices = [int(n) for n in re.findall(r"\d+", selection_response.text)][:NUM_PICKS]
    chosen = [articles[c - 1] for c in choices]

    # 5) Build the article block
    article_block = ""
    for rank, a in enumerate(chosen, start=1):
        snippet = getattr(a, "summary", "")
        article_block += (
            f"Article {rank}:\nTitle: {a.title}\nSnippet: {snippet}\nURL: {a.link}\n\n"
        )

    # 6) Detailed summaries
    summary_prompt = (
        "You are writing a daily health and illness awareness digest for an everyday "
        "reader. For EACH of the three articles below, open the URL and read the full "
        "article, then write a detailed but easy-to-read summary aimed at a 2-3 minute "
        "read time per article (roughly 280-450 words each).\n\n"
        "Each summary should cover:\n"
        "  - what happened and the key facts and figures\n"
        "  - the background and context a reader needs to make sense of it\n"
        "  - any numbers, studies, or expert quotes worth knowing\n"
        "  - how this fits the bigger picture (trends, history, comparable events)\n"
        "  - what this means for ordinary people's health and wellbeing\n"
        "  - practical things a reader could watch for, ask their doctor, or be aware of\n"
        "  - one short closing line summing up why this matters\n\n"
        "Write in plain, confident English that a general adult reader can follow. "
        "Translate medical or technical jargon into normal words. Use short paragraphs, "
        "no bullet points inside the summary, no headings. Stay neutral and factual; "
        "do not give personal medical advice. If a URL cannot be read, write the best "
        "summary you can from the title and snippet and note '(based on headline only)' "
        "at the end.\n\n"
        + article_block
        + "Format your reply EXACTLY like this, no JSON, no code fences, no extra commentary:\n\n"
        "=== SUMMARY 1 ===\n<your summary of article 1>\n\n"
        "=== SUMMARY 2 ===\n<your summary of article 2>\n\n"
        "=== SUMMARY 3 ===\n<your summary of article 3>"
    )
    summary_response = call_ai(client, summary_prompt, use_url_context=True)
    parts = re.split(r"===\s*SUMMARY\s*\d+\s*===", summary_response.text.strip())
    summaries = [p.strip() for p in parts[1:]]

    # 7) Compose the email body
    lines = ["Good morning! Here is your daily health & illness awareness digest.\n"]
    for rank, a in enumerate(chosen, start=1):
        text = summaries[rank - 1] if rank - 1 < len(summaries) else "(summary missing)"
        lines += [f"{rank}. {a.title}", "", text, "", f"Read more: {a.link}",
                  "\n" + "-" * 60 + "\n"]
    body = "\n".join(lines)

    # 8) Send
    send_email("Daily health & illness digest", body)
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
            # If even the failure email fails, just give up loudly.
            print("Could not send failure email either.")
            raise
