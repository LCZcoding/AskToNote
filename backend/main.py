from fastapi import FastAPI

app = FastAPI()

profile = {
    "heroTitle": "测试fastapi",
    "heroSubtitle": "2026.10.11",
}

@app.get("/api/profile")
def get_profile():
    return profile
