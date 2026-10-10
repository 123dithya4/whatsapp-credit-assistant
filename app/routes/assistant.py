from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.actions import run_call
from app.services.llm import LLMError, parse_message

router = APIRouter(prefix="/assistant", tags=["assistant"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/message")
def handle_message(body: MessageIn, db: Session = Depends(get_db)):
    try:
        parsed = parse_message(body.text)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    replies = [run_call(db, c.name, c.args) for c in parsed.calls]
    replies += [f"⚠️ {e}" for e in parsed.errors]
    replies += [f"❓ {c.question}" for c in parsed.clarifications]
    if not replies:
        replies = [parsed.reply or "Sorry, I didn't understand that. Please try again."]

    return {
        "parsed": [{"tool": c.name, "args": c.args.model_dump(mode="json")} for c in parsed.calls],
        "reply": "\n".join(replies),
    }
