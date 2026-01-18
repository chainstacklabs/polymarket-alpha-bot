"""FastAPI application for Alphapoly."""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # Load .env file
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from server import __version__
from server.price_aggregation import price_aggregation
from server.routers import data, pipeline, prices
from core.market_poller import market_poller


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - start/stop background services."""
    # Startup
    logger.info("Starting Alphapoly API...")
    await price_aggregation.start()
    await market_poller.start()

    yield

    # Shutdown
    logger.info("Shutting down Alphapoly API...")
    await market_poller.stop()
    await price_aggregation.stop()


app = FastAPI(
    title="Alphapoly API",
    description="API for Polymarket alpha detection pipeline",
    version=__version__,
    lifespan=lifespan,
)

# CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(data.router, prefix="/data", tags=["data"])
app.include_router(prices.router, prefix="/prices", tags=["prices"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])

# Portfolio real-time updates
from server.routers import portfolio_prices

app.include_router(portfolio_prices.router, prefix="/portfolios", tags=["portfolios"])


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Alphapoly API",
        "version": __version__,
        "docs": "/docs",
        "endpoints": {
            "data": "/data",
            "prices": "/prices",
            "pipeline": "/pipeline",
            "portfolios": "/portfolios",
        },
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
