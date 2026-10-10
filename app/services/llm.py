import logging
import json
from dataclasses import dataclass, field

from groq import Groq, GroqError
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.services.entries import Clarification, resolve_entry
from app.schemas import (
    GetBalanceArgs,
    GetPaymentsArgs,
    GetTopDebtorsArgs,
    GetTopPayersArgs,
    NoArgs,
    RecordTransactionArgs,
    RawTransactionArgs,
)

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """The LLM service failed (bad key, network, Groq error)."""

SYSTEM_PROMPT = """You are the bookkeeping assistant for a small shop in India.
The shopkeeper sends short messages (English, possibly mixed with Hindi, Kannada, Tamil or Malayalam words).
Turn each message into tool calls. You never write replies yourself: always call a tool.

First decide: is the message an ENTRY (something that happened) or a QUESTION (asking for information)?

ENTRIES -> record_transaction:
- "took on credit", "udhaar liya" -> type "credit".
- "paid", "gave back", "diya" (with an amount) -> type "payment".
- Put the amount as a number in "amount" and copy the exact words or digits that state it into "amount_quote", in the message's own language and script.
- NEVER guess, calculate or assume a missing value. If the message does not state an amount, leave amount and amount_quote out. If it is unclear whether the customer took credit or paid, leave type out. If no customer is named, leave customer out.
- Write the item in simple lowercase English (e.g. "rice"). Omit it for payments or if not mentioned.
- A message may contain several entries; make one tool call for each.

QUESTIONS -> never call record_transaction:
- How much does X owe / X ka kitna baaki hai -> get_balance
- How much did X pay / X ne kitna diya -> get_payments
- Who owes the most / biggest debtors -> get_top_debtors
- Who paid the most / who gave me the most money -> get_top_payers
- Today's entries / what happened today -> get_today_transactions

OTHER:
- If the message is a question or request that no tool can answer, or is unrelated to the shop ledger, call unsupported_request.

General rules:
- Write customer names and items in English (Latin) letters, transliterating if the message uses another script. Use the name as spoken, without honorifics such as bhai, ji, anna.
- Never invent names, amounts or items."""

TOOLS = [
      {
        "type": "function",
        "function": {
            "name": "record_transaction",
            "description": "Record that a customer took goods on credit, or paid money back. Leave out any value the message does not clearly state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "string", "description": "Customer name; omit if not stated"},
                    "amount": {"type": "number", "description": "Amount in rupees; omit if not stated"},
                    "amount_quote": {
                        "type": "string",
                        "description": "The exact words or digits copied from the message that state the amount, e.g. '500' or 'five hundred'; omit if no amount is stated",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["credit", "payment"],
                        "description": "credit = took goods and owes money; payment = paid money back; omit if unclear",
                    },
                    "item": {"type": "string", "description": "Goods taken, e.g. rice"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "How much a specific customer currently owes.",
            "parameters": {
                "type": "object",
                "properties": {"customer": {"type": "string"}},
                "required": ["customer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payments",
            "description": "How much a specific customer has paid back so far, with recent payments.",
            "parameters": {
                "type": "object",
                "properties": {"customer": {"type": "string"}},
                "required": ["customer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_debtors",
            "description": "List the customers who owe the most money.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "How many to list, default 5"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_today_transactions",
            "description": "List all entries recorded today.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
        {
        "type": "function",
        "function": {
            "name": "get_top_payers",
            "description": "List the customers who have paid the most money in total.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "How many to list, default 5"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unsupported_request",
            "description": "Use when the message is a question or request that no other tool can answer, or is unrelated to the shop ledger.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

ARG_MODELS: dict[str, type[BaseModel]] = {
    "record_transaction": RawTransactionArgs,
    "get_balance": GetBalanceArgs,
    "get_payments": GetPaymentsArgs,
    "get_top_debtors": GetTopDebtorsArgs,
    "get_today_transactions": NoArgs,
    "get_top_payers": GetTopPayersArgs,
    "unsupported_request": NoArgs,
}


@dataclass
class ToolCall:
    name: str
    args: BaseModel


@dataclass
class ParsedMessage:
    clarifications: list[Clarification] = field(default_factory=list)  # questions about incomplete entries
    calls: list[ToolCall] = field(default_factory=list)
    reply: str | None = None          # clarifying question / chat text when no tool was called
    errors: list[str] = field(default_factory=list)  # tool calls that failed validation


def _client() -> Groq:
    if not settings.groq_api_key:
        raise LLMError("GROQ_API_KEY is not set in .env")
    return Groq(api_key=settings.groq_api_key)


def parse_message(text: str) -> ParsedMessage:
    """Send the message to Groq and validate whatever tool calls come back."""
    try:
        response = _client().chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=TOOLS,
            tool_choice="auto",
            temperature=0,
        )
    except GroqError as exc:
        raise LLMError(f"Groq request failed: {exc}") from exc

    message = response.choices[0].message
    parsed = ParsedMessage(reply=message.content)
    logger.info("LLM tool calls: %s", [(tc.function.name, tc.function.arguments) for tc in message.tool_calls or []])

    for tc in message.tool_calls or []:
        model = ARG_MODELS.get(tc.function.name)
        if model is None:
            parsed.errors.append(f"Unknown tool '{tc.function.name}'")
            continue
        try:
            args = model.model_validate(json.loads(tc.function.arguments or "{}"))
        except (json.JSONDecodeError, ValidationError) as exc:
            parsed.errors.append(f"Could not understand the arguments for '{tc.function.name}': {exc}")
            continue

        if tc.function.name == "record_transaction":
            result = resolve_entry(args, text)
            if isinstance(result, RecordTransactionArgs):
                parsed.calls.append(ToolCall("record_transaction", result))
            else:
                parsed.clarifications.append(result)  # incomplete: ask, never guess
        else:
            parsed.calls.append(ToolCall(tc.function.name, args))

    return parsed
