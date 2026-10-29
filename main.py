"""ASGI entry point for Uvicorn and Vercel."""

from nevis_search.app import create_app

app = create_app()
