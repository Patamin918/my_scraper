"""
Reddit Comment Scraper + Claude Drafter
u/ShowerSlight — Excel/finance/freelance persona
Run during 8-10 PM Bangkok window
"""

import praw
import requests
import json
import os
from datetime import datetime, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────────
REDDIT_CLIENT_ID     = "YOUR_CLIENT_ID"
REDDIT_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REDDIT_USER_AGENT    = "reddit_scraper/1.0 by ShowerSlight"

ANTHROPIC_API_KEY    = "YOUR_ANTHROPIC_API_KEY"  # or set as env var

LOG_FILE = "reddit_log.json"

# Subreddits to watch — ranked by persona fit
SUBREDDITS = [
    "freelance",
    "personalfinance",
    "sidehustle",
    "Excel",
]

# Post filters
MIN_AGE_MINUTES  = 20
MAX_AGE_MINUTES  = 90
MAX_UPVOTES      = 50
MAX_COMMENTS     = 10

# ── PERSONA PROMPT ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are helping u/ShowerSlight write Reddit comments for karma building.

PERSONA:
- Excel power user, finance-aware, building passive income on the side
- Tone: genuine, direct, sounds like a real person not a marketer
- Never mention any product, link, or Gumroad — ever

COMMENT RULES:
- 2-4 sentences MAX for advice comments
- For Excel formula questions: paste a working formula in a code block — short, clean
- Lead with the solution, not the explanation
- Sound like someone who uses Excel daily, not a teacher
- No "great question!", no "I hope this helps!", no em dashes
- Simple language — if a 10 year old can't follow the logic, simplify it
- Must add real value — not generic advice anyone could Google
- Read ALL existing comments first — if already solved, say SKIP

QUALITY CHECK (answer all before drafting):
1. Is there a good answer already in existing comments? → if yes, say SKIP
2. Does my answer add something new?
3. Is it simple and human sounding?
4. Am I staying in persona (Excel/finance/freelance)?

OUTPUT FORMAT:
If worth commenting, respond with:
VERDICT: COMMENT
DRAFT:
[your comment here]
REASON: [one sentence why this adds value]

If not worth commenting, respond with:
VERDICT: SKIP
REASON: [one sentence why]
"""

# ── REDDIT SETUP ───────────────────────────────────────────────────────────────
def get_reddit():
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )

# ── FETCH QUALIFYING POSTS ─────────────────────────────────────────────────────
def get_qualifying_posts(reddit):
    now = datetime.now(timezone.utc)
    qualifying = []

    for sub_name in SUBREDDITS:
        print(f"\n📡 Scanning r/{sub_name} — Rising...")
        sub = reddit.subreddit(sub_name)

        for post in sub.rising(limit=50):
            # Age check
            created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
            age_minutes = (now - created).total_seconds() / 60

            if age_minutes < MIN_AGE_MINUTES or age_minutes > MAX_AGE_MINUTES:
                continue

            # Upvote + comment check
            if post.score > MAX_UPVOTES:
                continue
            if post.num_comments > MAX_COMMENTS:
                continue

            # Skip if no text (link posts)
            if not post.selftext or post.selftext.strip() == "":
                continue

            qualifying.append({
                "subreddit":    sub_name,
                "post_id":      post.id,
                "title":        post.title,
                "body":         post.selftext[:2000],  # cap at 2000 chars
                "age_minutes":  round(age_minutes, 1),
                "upvotes":      post.score,
                "num_comments": post.num_comments,
                "url":          f"https://reddit.com{post.permalink}",
                "comments":     get_top_comments(post),
            })

    return qualifying

# ── FETCH EXISTING COMMENTS ────────────────────────────────────────────────────
def get_top_comments(post):
    post.comments.replace_more(limit=0)
    comments = []
    for c in post.comments[:5]:  # top 5 only
        comments.append({
            "author": str(c.author),
            "body":   c.body[:500],
            "score":  c.score,
        })
    return comments

# ── CALL CLAUDE API ────────────────────────────────────────────────────────────
def draft_comment(post):
    api_key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")

    # Build user message with full post context
    comments_text = ""
    if post["comments"]:
        comments_text = "\n\nEXISTING COMMENTS:\n"
        for c in post["comments"]:
            comments_text += f"- u/{c['author']} ({c['score']} upvotes): {c['body']}\n"
    else:
        comments_text = "\n\nEXISTING COMMENTS: None yet."

    user_message = f"""
SUBREDDIT: r/{post['subreddit']}
POST TITLE: {post['title']}
POST AGE: {post['age_minutes']} minutes old
UPVOTES: {post['upvotes']}

POST BODY:
{post['body']}
{comments_text}

Should I comment on this? If yes, draft the comment following all rules.
"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type":         "application/json",
            "x-api-key":            api_key,
            "anthropic-version":    "2023-06-01",
        },
        json={
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system":     SYSTEM_PROMPT,
            "messages":   [{"role": "user", "content": user_message}],
        }
    )

    if response.status_code != 200:
        print(f"  ❌ API error: {response.status_code} — {response.text}")
        return None

    return response.json()["content"][0]["text"]

# ── LOG RESULT ─────────────────────────────────────────────────────────────────
def log_result(post, verdict, draft, reason):
    entry = {
        "timestamp":    datetime.now().isoformat(),
        "subreddit":    post["subreddit"],
        "title":        post["title"],
        "url":          post["url"],
        "age_minutes":  post["age_minutes"],
        "upvotes":      post["upvotes"],
        "num_comments": post["num_comments"],
        "verdict":      verdict,
        "draft":        draft,
        "reason":       reason,
        "posted":       False,  # you manually flip this to True after posting
    }

    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)

    logs.append(entry)

    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

# ── PARSE CLAUDE RESPONSE ──────────────────────────────────────────────────────
def parse_response(text):
    verdict = "SKIP"
    draft   = ""
    reason  = ""

    lines = text.strip().split("\n")
    mode  = None

    for line in lines:
        if line.startswith("VERDICT:"):
            verdict = line.replace("VERDICT:", "").strip()
        elif line.startswith("DRAFT:"):
            mode = "draft"
        elif line.startswith("REASON:"):
            mode = "reason"
            reason = line.replace("REASON:", "").strip()
        elif mode == "draft":
            draft += line + "\n"
        elif mode == "reason" and not reason:
            reason = line.strip()

    return verdict.upper(), draft.strip(), reason.strip()

# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("🤖 Reddit Scraper — u/ShowerSlight")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')} Bangkok")
    print("=" * 60)

    reddit     = get_reddit()
    posts      = get_qualifying_posts(reddit)
    candidates = 0

    if not posts:
        print("\n❌ No qualifying posts found. Try again in 20 min.")
        return

    print(f"\n✅ Found {len(posts)} qualifying post(s). Sending to Claude...\n")

    for post in posts:
        print(f"─" * 60)
        print(f"📌 r/{post['subreddit']} — {post['title'][:60]}")
        print(f"   Age: {post['age_minutes']}min | Upvotes: {post['upvotes']} | Comments: {post['num_comments']}")
        print(f"   URL: {post['url']}")

        response_text = draft_comment(post)
        if not response_text:
            continue

        verdict, draft, reason = parse_response(response_text)

        if verdict == "COMMENT":
            candidates += 1
            print(f"\n   ✅ WORTH COMMENTING")
            print(f"   Reason: {reason}")
            print(f"\n   DRAFT:")
            print(f"   {'─'*40}")
            for line in draft.split("\n"):
                print(f"   {line}")
            print(f"   {'─'*40}")
            log_result(post, "COMMENT", draft, reason)
        else:
            print(f"   ⏭️  SKIP — {reason}")
            log_result(post, "SKIP", "", reason)

    print(f"\n{'='*60}")
    print(f"✅ Done. {candidates} comment candidate(s) found.")
    print(f"📝 All results logged to {LOG_FILE}")
    print(f"👉 Copy the draft above, paste it manually on Reddit.")
    print(f"👉 Open {LOG_FILE} and set 'posted': true after posting.")
    print("=" * 60)


if __name__ == "__main__":
    main()
