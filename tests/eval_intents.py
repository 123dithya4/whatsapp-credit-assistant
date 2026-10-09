"""Run with:  python -m tests.eval_intents  (needs GROQ_API_KEY, uses API calls)"""
from app.services.llm import parse_message

CASES = [
    ("How much does Ramesh owe?", "get_balance"),
    ("Who owes me the most?", "get_top_debtors"),
    ("Show today's transactions", "get_today_transactions"),
    ("How much did Ramesh pay?", "get_payments"),
    ("Ramesh took 500 rupees of rice on credit", "record_transaction"),
    ("Ramesh paid 200", "record_transaction"),
    ("Ramesh bhai ka kitna baaki hai?", "get_balance"),
    ("Suresh ne kitna diya?", "get_payments"),
    ("aaj kya kya hua", "get_today_transactions"),
]


def main():
    passed = 0
    for text, expected in CASES:
        parsed = parse_message(text)
        got = parsed.calls[0].name if parsed.calls else None
        ok = got == expected
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {text!r}  expected={expected}  got={got}")
    print(f"\n{passed}/{len(CASES)} correct")


if __name__ == "__main__":
    main()
