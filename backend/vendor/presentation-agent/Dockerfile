ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress fontconfig fonts-dejavu-core fonts-liberation2 fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/pptx
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends python3-uno \
    && rm -rf /var/lib/apt/lists/*
COPY app ./app
COPY runtime ./runtime
COPY bridge.py sandbox_entry.py render_worker.py uno_convert.py ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/work/home TMPDIR=/work/tmp \
    PRESENTATION_FONT_DIR=/fonts LANG=C.UTF-8
USER 65532:65532
ENTRYPOINT ["timeout", "--signal=KILL", "210", "python", "-I", "/opt/pptx/sandbox_entry.py"]
