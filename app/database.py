from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./credit_assistant.db"

# check_same_thread=False is needed because FastAPI uses multiple threads
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: gives a session per request, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()