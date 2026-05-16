import os

from sqlmodel import Session, create_engine
from datetime import datetime
from models import Record, Analysis, Ibis
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def seed_database():
    with Session(engine) as session:
        record_1 = Record(
            images=["https://supabase.co/storage/v1/object/public/images/guara1.png", "https://supabase.co/storage/v1/object/public/images/guara2.png"],
            latitude_camera=-24.7088,
            longitude_camera=-47.5582,
            behavior=["vocalizando"],
            date_time=datetime.now(),
            user_id=1
        )
        session.add(record_1)
        session.flush()

        analysis_1 = Analysis(
            ibis_quantity=2,
            flock_size="pequeno",
            latitude=-24.7089,
            longitude=-47.5581,
            datetime=datetime.now(),
            recorder_id=record_1.id
        )
        session.add(analysis_1)
        session.flush()

        ibis_1 = Ibis(color="vermelho", age_group="adulto", analysis_id=analysis_1.id)
        ibis_2 = Ibis(color="cinza", age_group="juvenil", analysis_id=analysis_1.id)
        session.add_all([ibis_1, ibis_2])

        record_2 = Record(
            images=["https://supabase.co/storage/v1/object/public/images/guara2.png"],
            latitude_camera=-24.7100,
            longitude_camera=-47.5600,
            behavior=["alimentando-se"],
            date_time=datetime.now(),
            user_id=1
        )
        session.add(record_2)
        session.flush()

        analysis_2 = Analysis(
            ibis_quantity=1,
            flock_size="individual",
            latitude=-24.7101,
            longitude=-47.5599,
            datetime=datetime.now(),
            recorder_id=record_2.id
        )
        session.add(analysis_2)
        session.flush()

        ibis_3 = Ibis(color="vermelho", age_group="adulto", analysis_id=analysis_2.id)
        session.add(ibis_3)

        record_3 = Record(
            images=["https://supabase.co/storage/v1/object/public/images/vazio.png"],
            latitude_camera=-24.7050,
            longitude_camera=-47.5500,
            behavior=[],
            date_time=datetime.now(),
            user_id=1
        )
        session.add(record_3)
        session.flush()

        analysis_3 = Analysis(
            ibis_quantity=0,
            flock_size="nenhum",
            latitude=-24.7051,
            longitude=-47.5499,
            datetime=datetime.now(),
            recorder_id=record_3.id
        )
        session.add(analysis_3)
        
        session.commit()
        print("Seed executado com sucesso!")

if __name__ == "__main__":
    seed_database()