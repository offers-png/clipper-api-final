FROM python:3.13-slim

WORKDIR /app
COPY . .

RUN apt-get update && apt-get install -y ffmpeg libsm6 libxext6
RUN pip install -r requirements.txt

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
