import os
from sqlalchemy import create_engine, text
with create_engine(os.environ['LAUNCHLMS_SQL_CONNECTION_STRING']).connect() as conn:
    assert conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar() == 'vector'
    assert conn.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='resourcesearchdocument'::regclass AND attname='embedding'")).scalar() == 'vector(384)'
    assert conn.execute(text("SELECT indexname FROM pg_indexes WHERE indexname='ix_resourcesearchdocument_embedding_hnsw'")).scalar()
