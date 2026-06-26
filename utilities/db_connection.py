import os
from contextlib import contextmanager
from sqlalchemy import create_engine, MetaData

_engine = None
metadata = MetaData()


def get_engine():
    global _engine
    if _engine is None:
        db_url = os.environ.get("DB_URL", "")
        if not db_url:
            raise RuntimeError("DB_URL environment variable not set")
        _engine = create_engine(
            db_url,
            pool_size=1,
            max_overflow=2,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False,
        )
        metadata.reflect(bind=_engine)
    return _engine


@contextmanager
def get_connection():
    engine = get_engine()
    with engine.connect() as conn:
        with conn.begin():
            yield conn
