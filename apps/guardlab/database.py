import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


DATABASE_URL = os.getenv('DATABASE_URL', f"sqlite:///{Path(__file__).with_name('security.db')}")
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(bind=engine)


def get_db():
    with SessionLocal() as session:
        yield session
