from fastapi import FastAPI
from routes import user, record, analysis, ibis
from database import engine, SessionLocal
from sqlmodel import SQLModel
from datetime import datetime
from models import User, Record, Analysis, Ibis

app = FastAPI()


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # simple seed: create one admin user and related sample rows if DB is empty
        if db.query(User).first() is None:
            admin = User(name="admin", email="admin@example.com")
            db.add(admin)
            db.commit()

            # create one sample analysis and ibis and record linked to admin
            sample_record = Record(images="[]", latitude_camera=0.0, longitude_camera=0.0, behavior="none", date_time=datetime(1970,1,1), user_id=admin.id)
            db.add(sample_record)
            db.commit()

            sample_analysis = Analysis(ibis_quantity=0, flock_size="small", latitude=0.0, longitude=0.0, datetime=datetime(1970,1,1), recorder_id=admin.id)
            db.add(sample_analysis)
            db.commit()

            sample_ibis = Ibis(color="unknown", age_group="unknown", analysis_id=sample_analysis.id)
            db.add(sample_ibis)
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