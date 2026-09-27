#!/usr/bin/env python3
"""
Gmail Daily Briefing Script
Fetches recent emails from multiple Gmail accounts and uses Gemini (free tier)
to summarize them and suggest actions.

Usage:
  gmail_brief.py --add-account        # OAuth login for one more Gmail account
  gmail_brief.py --list-accounts      # Show configured accounts
  gmail_brief.py                      # Brief for all accounts (last 24h) + AI analysis
  gmail_brief.py --query "is:unread newer_than:3d" --no-ai

Gemini key: set GEMINI_API_KEY in the environment or in gmail_brief/.env
(get a free key at https://aistudio.google.com/apikey).
"""

import os
import re
import sys
import json
import base64
import datetime
import argparse
import html
import urllib.request
import urllib.error
from pathlib import Path

# Google API imports
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    print("Error: Required packages not installed. Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

BASE_DIR = Path(__file__).resolve().parent
TOKENS_DIR = BASE_DIR / 'tokens'
CREDENTIALS_PATH = BASE_DIR / 'credentials.json'
BRIEFS_DIR = BASE_DIR / 'briefs'
ENV_PATH = BASE_DIR / '.env'

DEFAULT_GEMINI_MODEL = 'gemini-flash-latest'
BODY_CHARS = 1500          # body text per email sent to Gemini
PROMPT_CHAR_BUDGET = 400_000  # keep well under free-tier token limits


def load_env():
    """Load KEY=VALUE lines from gmail_brief/.env without overriding real env vars."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


# ---------------------------------------------------------------- Gmail auth

def token_path_for(email: str) -> Path:
    return TOKENS_DIR / f"token_{re.sub(r'[^a-zA-Z0-9]+', '_', email.lower())}.json"


def save_creds(creds, token_path: Path):
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    token_path.chmod(0o600)


def run_oauth_flow():
    if not CREDENTIALS_PATH.exists():
        print(f"[!] Error: credentials.json not found at {CREDENTIALS_PATH}")
        return None
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    # prompt=select_account so the user can pick a different Gmail account each time
    return flow.run_local_server(port=0, prompt='select_account consent')


def add_account():
    """Log in one more Gmail account and store its token as token_<email>.json."""
    print("[*] A browser window will open — choose the Gmail account to add.")
    creds = run_oauth_flow()
    if not creds:
        return
    service = build('gmail', 'v1', credentials=creds)
    email = service.users().getProfile(userId='me').execute()['emailAddress']
    save_creds(creds, token_path_for(email))
    print(f"[✓] Added {email}")


def load_service(token_path: Path):
    """Return Gmail service for an existing token, re-running OAuth if it can't be refreshed."""
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        refreshed = False
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except Exception as e:
                print(f"[!] Token refresh failed for {token_path.name}: {e}")
        if not refreshed:
            print(f"[*] Re-authorizing {token_path.stem} — pick the same account in the browser.")
            creds = run_oauth_flow()
            if not creds:
                return None
        save_creds(creds, token_path)
    return build('gmail', 'v1', credentials=creds)


def load_all_services():
    """Return [(email, service)] for every token file, migrating legacy token names to token_<email>.json."""
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    if not list(TOKENS_DIR.glob("token_*.json")):
        print("[*] No accounts configured yet. Adding the first one...")
        add_account()

    services, seen = [], set()
    for tf in sorted(TOKENS_DIR.glob("token_*.json")):
        service = load_service(tf)
        if not service:
            continue
        try:
            email = service.users().getProfile(userId='me').execute()['emailAddress']
        except Exception as e:
            print(f"[!] Could not read profile for {tf.name}: {e}")
            continue
        if email in seen:
            continue
        seen.add(email)
        canonical = token_path_for(email)
        if tf != canonical and not canonical.exists():
            tf.rename(canonical)
        services.append((email, service))
    return services


# ---------------------------------------------------------------- Gmail fetch

INVISIBLE_RE = re.compile('[\u034f\u00ad\u200b-\u200f\u2060\ufeff]')


def clean(text: str) -> str:
    return re.sub(r'\s+', ' ', INVISIBLE_RE.sub('', text)).strip()


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode('utf-8', errors='replace')


def extract_body(payload) -> str:
    """Return best-effort plain text from a message payload (prefers text/plain, falls back to stripped HTML)."""
    plain, rich = [], []

    def walk(part):
        mime = part.get('mimeType', '')
        data = part.get('body', {}).get('data')
        if data and mime == 'text/plain':
            plain.append(_decode(data))
        elif data and mime == 'text/html':
            rich.append(_decode(data))
        for sub in part.get('parts', []) or []:
            walk(sub)

    walk(payload)
    if plain:
        text = '\n'.join(plain)
    else:
        text = '\n'.join(rich)
        text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', text)
        text = re.sub(r'(?s)<[^>]+>', ' ', text)
        text = html.unescape(text)
    text = re.sub(r'\n\s*>.*', '', text)       # drop quoted reply lines
    return clean(text)


def fetch_emails(service, query, max_results):
    results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
    emails = []
    for msg in results.get('messages', []):
        try:
            detail = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        except Exception as err:
            print(f"Failed to fetch message {msg['id']}: {err}")
            continue
        payload = detail.get('payload', {})
        headers = {h['name']: h['value'] for h in payload.get('headers', [])}
        labels = detail.get('labelIds', [])
        emails.append({
            'id': msg['id'],
            'thread_id': detail.get('threadId'),
            'from': headers.get('From', 'Unknown Sender'),
            'subject': headers.get('Subject', '(No Subject)'),
            'date': headers.get('Date', ''),
            'unread': 'UNREAD' in labels,
            'important': 'IMPORTANT' in labels,
            'snippet': clean(html.unescape(detail.get('snippet', ''))),
            'body': extract_body(payload)[:BODY_CHARS],
        })
    return emails


def gmail_link(email_addr, thread_id):
    return f"https://mail.google.com/mail/?authuser={email_addr}#all/{thread_id}"


# ---------------------------------------------------------------- Gemini

PROMPT_HEADER = """Bạn là trợ lý điều hành, đọc email của tôi từ nhiều tài khoản Gmail và viết bản tóm lược buổi sáng bằng tiếng Việt, ngắn gọn, dạng Markdown.

Yêu cầu:
1. **Tổng quan** (2–3 câu): tình hình chung, có gì gấp.
2. **Việc cần làm ngay** — danh sách hành động đề xuất, sắp theo mức ưu tiên (🔴 Gấp / 🟠 Trong ngày / 🟢 Khi rảnh). Mỗi dòng: hành động cụ thể (trả lời ai, về việc gì, thanh toán, duyệt, gọi điện...), tài khoản liên quan, hạn chót nếu email có nêu, và link Gmail dạng [mở](link).
3. **Tóm tắt theo tài khoản** — mỗi tài khoản một mục; gom email cùng chủ đề; mỗi email quan trọng 1 dòng (người gửi – nội dung chính).
4. **Có thể bỏ qua** — gộp newsletter, quảng cáo, thông báo tự động thành 1–2 dòng (đếm số lượng, gợi ý hủy đăng ký nếu lặp lại nhiều).

Quy tắc: không bịa thông tin ngoài email; nếu nghi lừa đảo/phishing thì cảnh báo; số tiền, ngày tháng, mã đơn giữ nguyên.

=== DỮ LIỆU EMAIL ===
"""


def build_prompt(accounts):
    parts = [PROMPT_HEADER]
    for email_addr, emails in accounts:
        parts.append(f"\n##### TÀI KHOẢN: {email_addr} ({len(emails)} email)\n")
        for e in emails:
            flags = ', '.join(f for f, on in (('CHƯA ĐỌC', e['unread']), ('QUAN TRỌNG', e['important'])) if on)
            parts.append(
                f"---\nFrom: {e['from']}\nSubject: {e['subject']}\nDate: {e['date']}\n"
                f"Flags: {flags or '-'}\nLink: {gmail_link(email_addr, e['thread_id'])}\n"
                f"Nội dung: {e['body'] or e['snippet']}\n"
            )
    prompt = ''.join(parts)
    if len(prompt) > PROMPT_CHAR_BUDGET:
        prompt = prompt[:PROMPT_CHAR_BUDGET] + "\n[...đã cắt bớt do quá dài...]"
    return prompt


def gemini_analyze(prompt, api_key, model):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    })
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors='replace')
        return f"*[Gemini lỗi HTTP {e.code}]* {detail[:500]}"
    except Exception as e:
        return f"*[Gemini lỗi]* {e}"
    try:
        return ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts'])
    except (KeyError, IndexError):
        return f"*[Gemini không trả về nội dung]* {json.dumps(data)[:500]}"


# ---------------------------------------------------------------- Brief

def render_raw(accounts):
    lines = []
    for email_addr, emails in accounts:
        lines.append(f"## 📧 {email_addr} ({len(emails)})")
        if not emails:
            lines.append("  *Không có email mới.*\n")
            continue
        for idx, e in enumerate(emails, 1):
            mark = ('🔵 ' if e['unread'] else '') + ('⭐ ' if e['important'] else '')
            lines.append(f"{idx}. {mark}**{e['subject']}** — {e['from']}")
            lines.append(f"   {e['snippet']}  [mở]({gmail_link(email_addr, e['thread_id'])})")
        lines.append("")
    return '\n'.join(lines)


def generate_daily_brief(query, max_results, use_ai, model, save):
    services = load_all_services()
    if not services:
        return

    accounts = []
    for email_addr, service in services:
        print(f"[*] Fetching {email_addr} ...", file=sys.stderr)
        try:
            accounts.append((email_addr, fetch_emails(service, query, max_results)))
        except Exception as e:
            print(f"[!] Failed to fetch {email_addr}: {e}", file=sys.stderr)

    out = [f"# 📬 Gmail Brief — {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
           f"_Query: `{query}` · {len(accounts)} tài khoản · {sum(len(e) for _, e in accounts)} email_\n"]

    total = sum(len(e) for _, e in accounts)
    if use_ai and total:
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            out.append("> ⚠️ Thiếu GEMINI_API_KEY (đặt trong env hoặc gmail_brief/.env) — bỏ qua phân tích AI.\n")
        else:
            print(f"[*] Analyzing with {model} ...", file=sys.stderr)
            out.append(gemini_analyze(build_prompt(accounts), api_key, model))
            out.append("\n---\n# 📄 Danh sách email gốc\n")

    out.append(render_raw(accounts))
    text = '\n'.join(out)
    print(text)

    if save:
        BRIEFS_DIR.mkdir(exist_ok=True)
        path = BRIEFS_DIR / f"brief_{datetime.datetime.now().strftime('%Y-%m-%d_%H%M')}.md"
        path.write_text(text)
        print(f"\n[✓] Saved to {path}", file=sys.stderr)


if __name__ == "__main__":
    load_env()
    parser = argparse.ArgumentParser(description="Multi-account Gmail brief with Gemini summary & suggested actions.")
    parser.add_argument("--query", default="newer_than:1d -in:chats",
                        help="Gmail query string (default: 'newer_than:1d -in:chats')")
    parser.add_argument("--max", type=int, default=30, help="Max emails per account (default: 30)")
    parser.add_argument("--no-ai", action="store_true", help="Skip Gemini analysis")
    parser.add_argument("--model", default=os.environ.get('GEMINI_MODEL', DEFAULT_GEMINI_MODEL),
                        help=f"Gemini model (default: {DEFAULT_GEMINI_MODEL}, or GEMINI_MODEL env)")
    parser.add_argument("--no-save", action="store_true", help="Don't save the brief to briefs/")
    parser.add_argument("--add-account", action="store_true", help="Log in and add another Gmail account")
    parser.add_argument("--list-accounts", action="store_true", help="List configured accounts")
    args = parser.parse_args()

    if args.add_account:
        add_account()
    elif args.list_accounts:
        for tf in sorted(TOKENS_DIR.glob("token_*.json")):
            print(tf.stem.removeprefix("token_"))
    else:
        generate_daily_brief(args.query, args.max, not args.no_ai, args.model, not args.no_save)
