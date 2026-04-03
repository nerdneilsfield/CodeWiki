#!/usr/bin/env python3
"""
CodeWiki Web Application

A web interface for users to submit GitHub repositories for documentation generation.
Features:
- Simple web form for GitHub repo URL input
- Background processing queue
- Cache system for generated documentation
- Job status tracking
"""

import argparse
import logging
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.exception_handlers import http_exception_handler as _default_http_exc_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cache_manager import CacheManager
from .background_worker import BackgroundWorker
from .routes import WebRoutes
from .config import WebAppConfig
from .templates import NOT_FOUND_TEMPLATE
from codewiki.src.logging_setup import configure_web_logging

logger = logging.getLogger(__name__)


# Initialize FastAPI app
app = FastAPI(
    title="CodeWiki", description="Generate comprehensive documentation for any GitHub repository"
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Only return HTML 404 for browser routes; API routes must return JSON.
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return HTMLResponse(content=NOT_FOUND_TEMPLATE, status_code=404)
    return await _default_http_exc_handler(request, exc)


# Initialize components
cache_manager = CacheManager(
    cache_dir=WebAppConfig.CACHE_DIR, cache_expiry_days=WebAppConfig.CACHE_EXPIRY_DAYS
)
background_worker = BackgroundWorker(
    cache_manager=cache_manager,
    temp_dir=WebAppConfig.TEMP_DIR,
    config_path=WebAppConfig.CONFIG_PATH,
)
web_routes = WebRoutes(background_worker=background_worker, cache_manager=cache_manager)


# Register routes
@app.get("/", response_class=HTMLResponse)
async def index_get(request: Request):
    """Main page with form for submitting GitHub repositories."""
    return await web_routes.index_get(request)


@app.post("/", response_class=HTMLResponse)
async def index_post(request: Request, repo_url: str = Form(...), commit_id: str = Form("")):
    """Handle repository submission."""
    return await web_routes.index_post(request, repo_url, commit_id)


@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    """API endpoint to get job status."""
    return await web_routes.get_job_status(job_id)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """API endpoint to request job cancellation."""
    return await web_routes.cancel_job(job_id)


@app.get("/docs/{job_id}")
async def view_docs(job_id: str):
    """View generated documentation."""
    return await web_routes.view_docs(job_id)


@app.get("/static-docs/{job_id}/")
@app.get("/static-docs/{job_id}/{filename:path}")
async def serve_generated_docs(job_id: str, filename: str = "overview.md"):
    """Serve generated documentation files."""
    if not filename:
        filename = "overview.md"
    return await web_routes.serve_generated_docs(job_id, filename)


def main():
    """Main function to run the web application."""
    import uvicorn

    configure_web_logging()

    parser = argparse.ArgumentParser(
        description="CodeWiki Web Application - Generate documentation for GitHub repositories"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=WebAppConfig.DEFAULT_HOST,
        help=f"Host to bind the server to (default: {WebAppConfig.DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=WebAppConfig.DEFAULT_PORT,
        help=f"Port to run the server on (default: {WebAppConfig.DEFAULT_PORT})",
    )
    parser.add_argument("--debug", action="store_true", help="Run the server in debug mode")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to TOML config file (overrides CODEWIKI_CONFIG env var)",
    )

    args = parser.parse_args()

    # Propagate --config so that worker processes (uvicorn reload) inherit it.
    if args.config:
        import os as _os

        _os.environ["CODEWIKI_CONFIG"] = args.config
        background_worker.config_path = args.config

    # Ensure required directories exist
    WebAppConfig.ensure_directories()

    # Start background worker
    background_worker.start()

    logger.info("CodeWiki Web Application starting")
    logger.info("Server running at: http://%s:%s", args.host, args.port)
    logger.info(
        "Web paths: cache_dir=%s temp_dir=%s",
        WebAppConfig.get_absolute_path(WebAppConfig.CACHE_DIR),
        WebAppConfig.get_absolute_path(WebAppConfig.TEMP_DIR),
    )
    logger.info("Press Ctrl+C to stop the server")

    try:
        uvicorn.run(
            "fe.web_app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="debug" if args.debug else "info",
        )
    except KeyboardInterrupt:
        logger.info("Server stopped")
        background_worker.stop()


if __name__ == "__main__":
    main()
