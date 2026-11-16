FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 opensigweave \
    && useradd --system --uid 10001 --gid opensigweave --home-dir /var/lib/opensigweave opensigweave \
    && mkdir -p /var/lib/opensigweave \
    && chown -R opensigweave:opensigweave /var/lib/opensigweave

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /app/requirements.txt

COPY --chown=root:root . /app
RUN chmod -R a=rX /app

USER 10001:10001

EXPOSE 8090

CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && exec uvicorn app:app --host 0.0.0.0 --port 8090 --no-access-log"]
