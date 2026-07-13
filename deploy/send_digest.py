# -*- coding: utf-8 -*-
"""
Բիզնեսի զարկերակ — daily/weekly digest sender.

Проактивный алерт «где дыра»: тянет уже проверенные KPI-эндпоинты (localhost),
собирает короткий дайджест на армянском (здоровье, топ-проблемы в деньгах,
худшие территории, кому звонить) и отправляет в Telegram и/или Email.

READ-ONLY: только GET к своим же API; в БД не ходит напрямую.

Настройка (в .env):
  DIGEST_BASE_URL       = http://localhost:5000      (по умолчанию)
  DIGEST_PERIOD         = this-month | last-month     (по умолчанию this-month)
  TELEGRAM_BOT_TOKEN    = 123456:ABC...   (от @BotFather)
  TELEGRAM_CHAT_ID      = 12345678        (свой chat id; узнать у @userinfobot)
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, DIGEST_EMAIL_TO, DIGEST_EMAIL_FROM

Запуск:
  python deploy/send_digest.py            # отправить (куда настроено)
  python deploy/send_digest.py --dry-run  # только показать текст, не отправлять
  python deploy/send_digest.py --period last-month
"""
import os
import sys
import json
import html
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# Windows-консоль по умолчанию cp1252 — не печатает армянский/кириллицу. Переключаем на utf-8.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

try:
    from dotenv import load_dotenv
    _here = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_here, '..', '.env'))
except Exception:
    pass


def _fmt(v):
    try:
        return f"{float(v):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def _period(kind):
    """Возвращает (date_from, date_to, label). this-month = текущий календарный месяц,
    last-month = предыдущий полный месяц (для еженедельного/месячного дайджеста)."""
    today = datetime.now()
    if kind == 'last-month':
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        d_from = last_prev.replace(day=1)
        d_to = last_prev
    else:
        d_from = today.replace(day=1)
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        d_to = nxt - timedelta(days=1)
    return d_from.strftime('%Y-%m-%d'), d_to.strftime('%Y-%m-%d'), d_from.strftime('%Y-%m')


def _get(base, path):
    url = base.rstrip('/') + path
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.load(r)


def _plain(text):
    """Текст без HTML: убираем <b>/</b> и разэкранируем сущности (&amp; → &, &#x27; → ').
    Нужно для email/консоли — иначе имена типа «Рога & Копыта» показываются как «Рога &amp; Копыта»."""
    return html.unescape(text.replace('<b>', '').replace('</b>', ''))


def build_digest(base, date_from, date_to, label):
    # /health обязателен; /areas и /trend — опциональные секции, их сбой не должен ронять весь алерт
    health = _get(base, f"/api/managers/kpi/health?date_from={date_from}&date_to={date_to}")
    try:
        areas = _get(base, f"/api/managers/kpi/health/areas?date_from={date_from}&date_to={date_to}")
    except Exception:
        areas = {"success": False, "areas": []}
    try:
        trend = _get(base, f"/api/managers/kpi/health/trend?date_to={date_to}&months=12")
    except Exception:
        trend = {"success": False}

    if not health.get('success'):
        raise RuntimeError("health endpoint failed: %s" % health.get('error'))

    h = health
    score = h['health']['score']
    verdict = h['health']['verdict']

    lines = []
    lines.append(f"📊 <b>Բիզնեսի զարկերակ</b> · {label}")
    emoji = "🟢" if (score or 0) >= 80 else ("🟡" if (score or 0) >= 50 else "🔴")
    lines.append(f"{emoji} Առողջություն՝ <b>{score}/100</b> — {verdict}")
    rev = h['revenue']
    yoy = rev.get('yoy')
    yoy_s = (f" ({'+' if yoy >= 0 else ''}{yoy}% YoY)" if yoy is not None else "")
    lines.append(f"💰 Հասույթ՝ <b>{_fmt(rev['cur'])} ֏</b>{yoy_s} · Պարտք՝ {_fmt(h['cash']['debt'])} ֏")

    # Топ-проблемы в деньгах
    findings = h.get('findings', [])[:4]
    if findings:
        lines.append("")
        lines.append("🔴 <b>Որտեղ ենք կորցնում գումար՝</b>")
        for f in findings:
            impact = f" (−{_fmt(f['impact'])} ֏)" if f.get('impact') else ""
            lines.append(f"• {html.escape(f['title'])}{impact}")

    # Худшие территории
    bad_areas = [a for a in areas.get('areas', []) if a.get('score') is not None and not a.get('lowData')][:3]
    if bad_areas:
        lines.append("")
        lines.append("🗺️ <b>Ամենախնդրահարույց տարածքներ՝</b>")
        for a in bad_areas:
            iss = ", ".join(i['label'] for i in a.get('issues', [])[:2])
            iss = f" — {html.escape(iss)}" if iss else ""
            lines.append(f"• {html.escape(a['name'])}՝ {a['score']}{iss}")

    # Кому звонить (должники 60+)
    overdue = h['cash'].get('topOverdue', [])[:3]
    if overdue:
        lines.append("")
        lines.append("📞 <b>Ում զանգահарել (60+ օր)՝</b>")
        for c in overdue:
            ph = f" {html.escape(c['phone'])}" if c.get('phone') else ""
            age = f" · {c['age']} օր" if c.get('age') else ""
            lines.append(f"• {html.escape(c['name'])}{ph} — {_fmt(c['amt'])} ֏{age}")

    # Динамика
    if trend.get('success') and trend.get('summary'):
        s = trend['summary']
        def _arrow(key, higher):
            t = s.get(key) or {}
            d = t.get('delta')
            if d is None:
                return None
            good = (d >= 0) if higher else (d <= 0)
            return f"{'📈' if d >= 0 else '📉'} {abs(d)}%{'' if good else '⚠️'}"
        rev_a = _arrow('revenue', True)
        debt_a = _arrow('debt', False)
        parts = []
        if rev_a:
            parts.append(f"հասույթ {rev_a}")
        if debt_a:
            parts.append(f"պարտք {debt_a}")
        if parts:
            lines.append("")
            lines.append("📅 <b>Դինամիկա (12 ամիս)՝</b> " + " · ".join(parts))

    lines.append("")
    lines.append(f"🔗 http://localhost:5000/managers-kpi?date_from={date_from}&date_to={date_to}")
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true"
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
        return json.load(r).get("ok", False)


def send_email(text, subject):
    import smtplib
    from email.mime.text import MIMEText
    host = os.environ.get('SMTP_HOST')
    port = int(os.environ.get('SMTP_PORT', 587) or 587)
    user = os.environ.get('SMTP_USER')
    pwd = os.environ.get('SMTP_PASSWORD')
    to = os.environ.get('DIGEST_EMAIL_TO')
    frm = os.environ.get('DIGEST_EMAIL_FROM', user)
    if not (host and to):
        return False
    msg = MIMEText(_plain(text), 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = frm
    msg['To'] = to
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        if user and pwd:
            s.login(user, pwd)
        s.sendmail(frm, [t.strip() for t in to.split(',')], msg.as_string())
    return True


def main():
    dry = '--dry-run' in sys.argv
    kind = os.environ.get('DIGEST_PERIOD', 'this-month')
    for i, a in enumerate(sys.argv):
        if a == '--period' and i + 1 < len(sys.argv):
            kind = sys.argv[i + 1]
    base = os.environ.get('DIGEST_BASE_URL', 'http://localhost:5000')

    date_from, date_to, label = _period(kind)
    text = build_digest(base, date_from, date_to, label)

    # Всегда сохраняем последний дайджест (для отладки/архива)
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'last_digest.txt'), 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass

    plain = _plain(text)
    if dry:
        print("=== DRY-RUN (не отправлено) ===")
        print(plain)
        return 0

    sent = []
    tok = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if tok and chat:
        try:
            if send_telegram(tok, chat, text):
                sent.append('telegram')
        except Exception as e:
            print("Telegram error:", e, file=sys.stderr)
    try:
        if send_email(text, f"Բիզնեսի զարկերակ · {label}"):
            sent.append('email')
    except Exception as e:
        print("Email error:", e, file=sys.stderr)

    if sent:
        print("Отправлено:", ", ".join(sent))
        return 0
    print("Каналы не настроены (TELEGRAM_BOT_TOKEN/CHAT_ID или SMTP_*). Дайджест сохранён в deploy/last_digest.txt:")
    print(plain)
    return 0


if __name__ == '__main__':
    sys.exit(main())
