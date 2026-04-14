FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/<tbd>"

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

CMD ["python", "-m", "eo_mcp.server"]
