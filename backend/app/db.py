from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.settings import settings


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(settings.database_url, connect_args=_connect_args(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def ensure_result_columns() -> None:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE results ADD COLUMN IF NOT EXISTS raw_llm_output TEXT"))
        connection.execute(text("ALTER TABLE results ADD COLUMN IF NOT EXISTS validation_error TEXT"))
        connection.execute(text("ALTER TABLE results ADD COLUMN IF NOT EXISTS llm_prompt TEXT"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
