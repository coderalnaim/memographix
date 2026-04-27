FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/work/python:/work/benchmarks

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      cargo \
      curl \
      git \
      nodejs \
      npm \
      rustc \
      time \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir "maturin>=1.7,<2"

WORKDIR /work

CMD ["python", "benchmarks/run.py", "--help"]
