## Local
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

## Docker
docker build -t myapp:latest .
docker run --rm -p 8000:8000 myapp:latest
