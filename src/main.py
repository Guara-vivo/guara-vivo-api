from fastapi import FastAPI
from routes import user, record, analysis, ibis
from database import SessionLocal
from models import User

app = FastAPI()


@app.on_event("startup")
def on_startup():
    db = SessionLocal()
    try:
        # simple seed: create one admin user if DB is empty
        if db.query(User).first() is None:
            admin = User(name="admin", email="admin@example.com")
            db.add(admin)
            db.commit()
    finally:
        db.close()


app.include_router(user.router)
app.include_router(record.router)
app.include_router(analysis.router)
app.include_router(ibis.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
