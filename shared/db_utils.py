import sqlite3
import io

def bytes_to_connection(db_bytes: bytes) -> sqlite3.Connection:
    """
    Load raw DB bytes into an in-memory SQLite connection.
    The caller is responsible for closing the connection.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(db_bytes)
    except AttributeError:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tf.write(db_bytes)
            tf_path = tf.name
        conn.close()
        conn = sqlite3.connect(tf_path)
        conn._tmp_path = tf_path  # type: ignore
    return conn