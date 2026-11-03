# Multi-stage build: install dependencies separately
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /install /usr/local
COPY src/ ./src/
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000 9100
ENTRYPOINT ["python", "-m", "ethidx"]
CMD ["indexer"]
