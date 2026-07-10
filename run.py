"""Entry point: `python run.py` (or `uvicorn run:app` behind a reverse proxy)."""

from kitecast.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
