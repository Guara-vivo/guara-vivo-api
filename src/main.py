from fastapi import FastAPI, Depends
from routes import user, record, analysis, ibis
from database import get_db, Base, engine

Base.metadata.create_all(bind=engine)
app = FastAPI(lifespan=None)

app.include_router(user.router)
app.include_router(record.router)
app.include_router(analysis.router)
app.include_router(ibis.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)