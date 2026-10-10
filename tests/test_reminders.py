from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas import GetOverdueArgs
from app.services import reminders
from app.services import transactions as ledger
from app.services.actions import format_overdue, run_call


def credit_days_ago(db, name, amount, days):
    txn = ledger.add_transaction(db, name, amount).transaction
    txn.created_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()
    return txn


def test_overdue_lists_old_unpaid_balances(db):
    credit_days_ago(db, "Suresh", 300, 12)
    ledger.add_transaction(db, "Anil", 100)  # recent: not overdue
    result = ledger.get_overdue(db, min_days=7)
    assert [o.name for o in result] == ["Suresh"]
    assert result[0].days == 12 and result[0].balance == Decimal("300")


def test_payment_clears_oldest_credit_first(db):
    credit_days_ago(db, "Ramesh", 500, 10)
    ledger.add_transaction(db, "Ramesh", 200)  # recent credit
    ledger.record_payment(db, "Ramesh", 500)   # settles the old one
    assert ledger.get_overdue(db, min_days=7) == []


def test_settled_customer_is_not_overdue(db):
    credit_days_ago(db, "Mohan", 300, 20)
    ledger.record_payment(db, "Mohan", 300)
    assert ledger.get_overdue(db, min_days=7) == []


def test_message_format(db):
    credit_days_ago(db, "Suresh", 300, 12)
    text = format_overdue(ledger.get_overdue(db, 7), 7)
    assert "Suresh" in text and "300.00" in text and "12 days" in text and "Total overdue" in text


def test_overdue_command_reply(db):
    credit_days_ago(db, "Suresh", 300, 12)
    assert "Suresh" in run_call(db, "get_overdue", GetOverdueArgs(days=7))
    assert "No balances" in run_call(db, "get_overdue", GetOverdueArgs(days=30))


def test_job_sends_digest_to_owner(db, monkeypatch):
    credit_days_ago(db, "Suresh", 300, 12)
    sent = []
    monkeypatch.setattr(reminders, "SessionLocal", lambda: db)
    monkeypatch.setattr(reminders.settings, "owner_whatsapp", "whatsapp:+910000000000")
    monkeypatch.setattr(reminders.settings, "reminder_after_days", 7)
    monkeypatch.setattr(reminders, "send_whatsapp", lambda to, body: sent.append((to, body)) or True)
    reminders.run_reminder_job()
    assert sent[0][0] == "whatsapp:+910000000000" and "Suresh" in sent[0][1]


def test_job_sends_nothing_when_nobody_is_overdue(db, monkeypatch):
    ledger.add_transaction(db, "Anil", 100)
    sent = []
    monkeypatch.setattr(reminders, "SessionLocal", lambda: db)
    monkeypatch.setattr(reminders.settings, "reminder_after_days", 7)
    monkeypatch.setattr(reminders, "send_whatsapp", lambda to, body: sent.append(body) or True)
    reminders.run_reminder_job()
    assert sent == []