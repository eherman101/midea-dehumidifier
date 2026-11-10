FROM python:3.11-slim

WORKDIR /app

# Exact versions (pip freeze of the image that ran until 2026-11-10); bump them on purpose, not by rebuilding.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8099

ENV APPLIANCE_IP=192.168.0.102
ENV PORT=8099

CMD ["python", "app.py"]
