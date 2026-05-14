FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY data ./data

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

ENV WEB_SOURCE=binance
ENV SYMBOL=BTCUSDT
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "SYMBOL_ARGS=\"\"; if [ -n \"$SYMBOLS\" ]; then SYMBOL_ARGS=\"--symbols $SYMBOLS\"; fi; crypto-tool web serve --source ${WEB_SOURCE:-binance} --symbol ${SYMBOL:-BTCUSDT} $SYMBOL_ARGS --host 0.0.0.0 --port ${PORT:-8080} --paper-journal ${PAPER_JOURNAL:-data/live-paper.jsonl}"]
