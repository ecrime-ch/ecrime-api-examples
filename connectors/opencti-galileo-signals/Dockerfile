FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/opencti-galileo-signals

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY opencti_galileo_signals ./opencti_galileo_signals

USER nobody

CMD ["python", "-m", "opencti_galileo_signals"]
