# syntax=docker/dockerfile:1
# Multi-stage build: stage 1 renders the static site from the committed
# SQLite database, stage 2 serves dist/ with the Python standard library.
# No dump download, Ollama, or network access is needed at runtime.
FROM python:3.14-slim AS build
WORKDIR /app
# Skip the optional C tokenizer of mwparserfromhell; the pure-Python fallback
# is enough for the one-off site build and keeps this stage toolchain-free.
ENV WITH_EXTENSION=0
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data/dic.sqlite data/source.json ./data/
RUN uv sync --frozen --no-dev \
 && uv run --frozen --no-dev dic build --database data/dic.sqlite --output dist

FROM python:3.14-slim
WORKDIR /site
COPY --from=build /app/dist ./
EXPOSE 8040
# HOST/PORT come from the container env (see scripts/docker/conventum/prod.env).
CMD ["sh", "-c", "python3 -m http.server ${PORT:-8040} --bind ${HOST:-0.0.0.0} --directory /site"]
