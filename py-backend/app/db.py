import os

from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.engine import URL, Engine

from app.logger import logger

_engine: Engine | None = None
metadata = MetaData()

# Table objects are reflected from the real database at connect time (see
# connect_sql_server below) rather than declared by hand here, so this file
# stays in sync with sql/schema.sql automatically instead of needing the
# column list maintained in two places.
brochures: Table | None = None
products: Table | None = None
b2b_companies: Table | None = None
b2b_products: Table | None = None


def build_engine_url() -> str | URL:
    """Builds the SQLAlchemy engine target from env vars.

    Which environment to connect to is picked by APP_ENV (defaults to
    "local" if not set) — set it to "local", "staging", or "production" and
    this looks for connection-string vars suffixed with that name, e.g.
    APP_ENV=staging picks up SQL_SERVER_ODBC_CONNECT_STAGING. This lets one
    .env file hold all three side by side without them colliding, and
    switching which one's active is a single-line change.

    For each environment, two ways to configure it, checked in order:

    1. SQL_SERVER_ODBC_CONNECT_<ENV> — a raw ODBC connection string, exactly
       what you'd put in any other ODBC-based tool. No URL-encoding needed,
       so a server name with a backslash (e.g. "localhost\\SQLEXPRESS")
       just works as-is. Recommended — this is the one that avoids the
       "Could not parse SQLAlchemy URL" error entirely. Example:

           SQL_SERVER_ODBC_CONNECT_LOCAL=DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost\\SQLEXPRESS;DATABASE=BrochureApp;Trusted_Connection=yes;TrustServerCertificate=yes;

    2. SQL_SERVER_CONNECTION_STRING_<ENV> — a full SQLAlchemy URL
       (mssql+pyodbc://...). Backslashes in the server name must be
       URL-encoded as %5C in this format.

    For backward compatibility, if neither suffixed variable is set for the
    active environment, this falls back to the old unsuffixed
    SQL_SERVER_ODBC_CONNECT / SQL_SERVER_CONNECTION_STRING — so existing
    single-environment .env files keep working untouched.
    """
    env = os.getenv("APP_ENV", "local").strip().lower()
    suffix = env.upper()

    odbc_str = os.getenv(f"SQL_SERVER_ODBC_CONNECT_{suffix}") or os.getenv("SQL_SERVER_ODBC_CONNECT")
    if odbc_str:
        logger.info(f"SQL Server target: {env} (ODBC connection string)")
        return URL.create("mssql+pyodbc", query={"odbc_connect": odbc_str})

    conn_str = os.getenv(f"SQL_SERVER_CONNECTION_STRING_{suffix}") or os.getenv("SQL_SERVER_CONNECTION_STRING")
    if conn_str:
        logger.info(f"SQL Server target: {env} (SQLAlchemy URL)")
        return conn_str

    logger.error(
        f"No connection string found for APP_ENV={env}. Expected "
        f"SQL_SERVER_ODBC_CONNECT_{suffix} or SQL_SERVER_CONNECTION_STRING_{suffix} "
        f"(or the unsuffixed fallback) in .env"
    )
    raise RuntimeError(f"No SQL Server connection string configured for APP_ENV={env}")


def connect_sql_server() -> None:
    """Connects to SQL Server and reflects the four tables (Brochures,
    Products, B2BCompanies, B2BProducts) created by sql/schema.sql. Call
    once at startup."""
    global _engine, brochures, products, b2b_companies, b2b_products

    if _engine is not None:
        return

    url = build_engine_url()

    try:
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        with _engine.connect() as conn:
            metadata.reflect(bind=conn, only=["Brochures", "Products", "B2BCompanies", "B2BProducts"])
        brochures = metadata.tables["Brochures"]
        products = metadata.tables["Products"]
        b2b_companies = metadata.tables["B2BCompanies"]
        b2b_products = metadata.tables["B2BProducts"]
        logger.info("SQL Server connected")
    except Exception as err:
        logger.error(f"SQL Server connection error: {err}")
        _engine = None
        raise


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("SQL Server is not connected yet")
    return _engine


def brochures_table() -> Table:
    if brochures is None:
        raise RuntimeError("SQL Server is not connected yet")
    return brochures


def products_table() -> Table:
    if products is None:
        raise RuntimeError("SQL Server is not connected yet")
    return products


def b2b_companies_table() -> Table:
    if b2b_companies is None:
        raise RuntimeError("SQL Server is not connected yet")
    return b2b_companies


def b2b_products_table() -> Table:
    if b2b_products is None:
        raise RuntimeError("SQL Server is not connected yet")
    return b2b_products