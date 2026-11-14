FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy==2.2.6
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid ${APP_GID} waterfall && useradd --uid ${APP_UID} --gid ${APP_GID} --no-create-home --home-dir /tmp waterfall
WORKDIR /app
COPY server.py /app/server.py
COPY index.html water_telemetry.py /app/
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
CMD ["python3", "-u", "/app/server.py"]
