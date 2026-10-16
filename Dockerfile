FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY examples ./examples

RUN useradd --create-home --uid 10001 pulsewatch
USER pulsewatch

VOLUME ["/data"]
ENV PULSEWATCH_DB=/data/pulsewatch.db

ENTRYPOINT ["pulsewatch"]
CMD ["run", "--targets", "/app/examples/targets.json", "--db", "/data/pulsewatch.db"]
