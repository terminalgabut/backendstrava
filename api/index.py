from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "FastAPI on Vercel is running!"}

@app.get("/health")
def health_check():
    return {"health": "ok"}
