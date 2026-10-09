import json
from dataclasses import dataclass, field

from groq import Groq, GroqError
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import (
    GetBalanceArgs,
    GetPaymentsArgs,
    GetTopDebtorsArgs,
    NoArgs,
    RecordTransactionArgs,
)


class LLMError(Exception):
    """The LLM service failed (bad key, network, Groq error)."""


SYSTEM_PROMPT = """You are the bookkeeping assistant for a small shop in India.
The shopkeeper sends short messages (English, possibly mixed with Hindi, Kannada or Malayalam words).
Turn each message into tool calls.

First decide: is the message an ENTRY (something that happened) or a QUESTION (asking for information)?

ENTRIES -> record_transaction:
- "took on credit", "udhaar liya" -> type "credit".
- "paid", "gave back", "diya" (with an amount) -> type "payment".
- Money is always in rupees. Convert spoken numbers ("five hundred") to digits.
- Write the item in simple lowercase English (e.g. "rice"). Omit it for payments or if not mentioned.
- A message may contain several entries; make one tool call for each.

QUESTIONS -> never call record_transaction:
- How much does X owe / X ka kitna baaki hai -> get_balance
- How much did X pay / X ne kitna diya -> get_payments
- Who owes the most / biggest debtors -> get_top_debtors
- Today's entries / what happened today -> get_today_transactions

General rules:
- Use the customer's name as spoken, without honorifics such as bhai, ji, anna.
- If a name or amount needed for an ENTRY is missing or unclear, do NOT call a tool. Ask one short clarifying question.
- Never invent names, amounts or items."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_transaction",
            "description": "Record that a customer took goods on credit, or paid money back.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "string", "description": "Customer name as spoken"},
                    "amount": {"type": "number", "description": "Amount in rupees, positive"},
                    "type": {
                        "type": "string",
                        "enum": ["credit", "payment"],
                        "description": "credit = took goods and owes money; payment = paid money back",
                    },
                    "item": {"type": "string", "description": "Goods taken, e.g. rice"},
                },
                "required": ["customer", "amount", "type"],
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
]

ARG_MODELS: dict[str, type[BaseModel]] = {
    "record_transaction": RecordTransactionArgs,
    "get_balance": GetBalanceArgs,
    "get_payments": GetPaymentsArgs,
    "get_top_debtors": GetTopDebtorsArgs,
    "get_today_transactions": NoArgs,
}


@dataclass
class ToolCall:
    name: str
    args: BaseModel


@dataclass
class ParsedMessage:
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

    for tc in message.tool_calls or []:
        model = ARG_MODELS.get(tc.function.name)
        if model is None:
            parsed.errors.append(f"Unknown tool '{tc.function.name}'")
            continue
        try:
            raw = json.loads(tc.function.arguments or "{}")
            parsed.calls.append(ToolCall(tc.function.name, model.model_validate(raw)))
        except (json.JSONDecodeError, ValidationError) as exc:
            parsed.errors.append(f"Could not understand the arguments for '{tc.function.name}': {exc}")

    return parsed
