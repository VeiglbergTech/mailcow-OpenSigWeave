FROM node:22-alpine AS ckeditor

WORKDIR /build
COPY ckeditor-entry.js ./ckeditor-entry.js
RUN npm install --prefix /tmp/ckeditor5 --no-save --no-package-lock ckeditor5@49.1.0 esbuild@0.24.2 \
    && mkdir -p /assets \
    && ln -s /tmp/ckeditor5/node_modules /build/node_modules \
    && /tmp/ckeditor5/node_modules/.bin/esbuild /build/ckeditor-entry.js --bundle --format=iife --outfile=/assets/ckeditor5.js \
    && cp /tmp/ckeditor5/node_modules/ckeditor5/LICENSE.md /assets/LICENSE.md

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 opensigweave \
    && useradd --system --uid 10001 --gid opensigweave --home-dir /var/lib/opensigweave opensigweave \
    && mkdir -p /var/lib/opensigweave \
    && chown opensigweave:opensigweave /var/lib/opensigweave

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /app/requirements.txt

COPY --chown=root:root . /app
COPY --from=ckeditor /assets/ckeditor5.js /app/assets/ckeditor5/49.1.0/ckeditor5.js
COPY --from=ckeditor /assets/ckeditor5.css /app/assets/ckeditor5/49.1.0/ckeditor5.css
COPY --from=ckeditor /assets/LICENSE.md /app/assets/ckeditor5/49.1.0/LICENSE.md
COPY --chmod=0755 entrypoint.sh /usr/local/bin/opensigweave-entrypoint
RUN chmod -R a=rX /app

EXPOSE 8090

ENTRYPOINT ["/usr/local/bin/opensigweave-entrypoint"]
CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && exec uvicorn app:app --host 0.0.0.0 --port 8090 --no-access-log"]
