ARG PYTHON_IMAGE_VERSION=3.12
ARG POETRY_VERSION=1.8.2
ARG EXTRA_PACKAGES=""

# Builder stage
FROM python:${PYTHON_IMAGE_VERSION}-slim AS builder

ARG POETRY_VERSION

RUN pip install poetry==${POETRY_VERSION}

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.create false

COPY . .
RUN poetry build

RUN VERSION=$(poetry version -s) && \
    echo "VERSION=${VERSION}" > version.env


FROM python:${PYTHON_IMAGE_VERSION}-slim AS runtime

ARG POETRY_VERSION
ARG EXTRA_PACKAGES

WORKDIR /app

COPY --from=builder /app/version.env .
COPY --from=builder /app/dist/*.whl .
COPY --from=builder /app/poetry.lock /app/pyproject.toml ./

RUN pip install poetry==${POETRY_VERSION}
RUN . ./version.env && pip install flux_core-${VERSION}-py3-none-any.whl
RUN poetry config virtualenvs.create false
RUN poetry install --no-dev --no-root
RUN pip install --no-cache-dir "${EXTRA_PACKAGES}"

COPY flux.toml ./

RUN mkdir .flux && mkdir .flux/.workflows && touch .flux/.workflows/__init__.py

ENTRYPOINT ["flux", "start", ".flux/.workflows" ]
