FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir midea-beautiful-air flask

COPY app.py .

EXPOSE 8080

ENV APPLIANCE_IP=192.168.0.102
ENV PORT=8080

CMD ["python", "app.py"]
