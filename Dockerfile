FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY staleguard ./staleguard
COPY examples ./examples
EXPOSE 8000
CMD ["uvicorn", "staleguard.server:app", "--host", "0.0.0.0", "--port", "8000"]
