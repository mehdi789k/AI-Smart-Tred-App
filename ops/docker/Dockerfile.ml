FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/lock/ml.txt /tmp/requirements.txt
ENV PIP_INDEX_URL=https://pypi.org/simple/
ENV PIP_CONFIG_FILE=/dev/null
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt

COPY scripts ./scripts
COPY src/python ./src/python
COPY market_data ./market_data
COPY alembic.ini .
COPY migrations ./migrations

RUN mkdir -p /app/models /app/backtest_results

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV MODEL_OUTPUT_DIR=/app/models

CMD ["python", "scripts/train_model.py", "--help"]
