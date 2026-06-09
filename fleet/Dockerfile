# Fleet server container. Build context = this fleet/ directory.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir flask gunicorn

# Copy the fleet package into /app/fleet so `fleet.server:app` imports cleanly.
COPY . /app/fleet/

EXPOSE 8080
ENV FLEET_DB=/data/fleet.db
VOLUME ["/data"]

# 2 workers is plenty for heartbeat traffic from dozens of boxes.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "fleet.server:app"]
