"""Main API entrypoint."""

import logging

from dotenv import load_dotenv
from fastapi import FastAPI

from db import check_database_connection

load_dotenv()

logging.basicConfig(level=logging.INFO)
from observability.langsmith_setup import log_langsmith_status

log_langsmith_status()

app = FastAPI(
    title="langgraph-nl2sql-system",
    description="Technical base for a multi-agent NL2SQL system.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck with database connectivity verification."""
    check_database_connection()
    return {"status": "healthy"}
