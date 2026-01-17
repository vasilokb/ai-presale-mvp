from fastapi import FastAPI

app = FastAPI(title="AI Presale MVP")

@app.get("/health")
def health():
    return {"status": "ok"}
