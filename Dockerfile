FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY solar_logger ./solar_logger
RUN pip install --no-cache-dir .
ENTRYPOINT ["solar-logger"]
CMD ["--config", "/config/config.yaml", "run"]
