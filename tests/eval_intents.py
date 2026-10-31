"""Live check of the real LLM. Run from the project folder:
    python -m tests.eval_intents      (needs GROQ_API_KEY in .env; uses API calls)"""
import time

from app.services.llm import parse_message

CASES = [
    ("Ramesh took 500 rupees of rice on credit", "record_transaction:credit:500"),
    ("Ramesh paid 200", "record_transaction:payment:200"),
    ("Ramesh ne paanch sau ka chawal udhaar liya", "record_transaction:credit:500"),
    ("How much does Ramesh owe?", "get_balance"),
    ("Ramesh bhai ka kitna baaki hai?", "get_balance"),
    ("How much did Ramesh pay?", "get_payments"),
    ("Suresh ne kitna diya?", "get_payments"),
    ("Who owes me the most?", "get_top_debtors"),
    ("Who paid the most?", "get_top_payers"),
    ("Show today's transactions", "get_today_transactions"),
    ("Who is overdue?", "get_overdue"),
    ("Ramesh took rice", "clarify"),            # missing amount
    ("Ramesh 500", "clarify"),                  # credit or payment?
    ("he took some rice", "clarify"),           # missing customer
    ("what is the weather today?", "unsupported_request"),
    ("asdf qwer", ("nothing", "unsupported_request")),
]


def outcome(parsed) -> str:
    if parsed.calls:
        call = parsed.calls[0]
        if call.name == "record_transaction":
            return f"record_transaction:{call.args.type.value}:{call.args.amount.normalize():f}"
        return call.name
    if parsed.clarifications:
        return "clarify"
    return "nothing"


def main() -> None:
    passed = 0
    for text, expected in CASES:
        accepted = expected if isinstance(expected, tuple) else (expected,)
        got = outcome(parse_message(text))
        ok = got in accepted
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {text!r}  expected={'/'.join(accepted)}  got={got}")
        time.sleep(0.5)
    print(f"\n{passed}/{len(CASES)} correct")
    raise SystemExit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()