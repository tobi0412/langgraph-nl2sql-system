"""Entrada principal de la API."""

from dotenv import load_dotenv
from fastapi import FastAPI

from db import check_database_connection

load_dotenv()

app = FastAPI(
    title="langgraph-nl2sql-system",
    description="Base tecnica para un sistema multiagente NL2SQL.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Healthcheck con verificacion de DB."""
    check_database_connection()
    return {"status": "healthy"}
