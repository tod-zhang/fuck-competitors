FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV FC_DB_URL=sqlite:////data/app.db
VOLUME /data
EXPOSE 9527 9528

# Single worker on purpose: the scheduler runs in-process (see app/scheduler.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9527", "--workers", "1"]
