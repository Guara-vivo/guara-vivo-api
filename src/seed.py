import asyncio
import os
from datetime import datetime

import bcrypt
from sqlalchemy import select

from models import User, Record, Analysis, Ibis
from database import AsyncSessionLocal

async def seed_database():
    async with AsyncSessionLocal() as session:
        # Check if admin should be created
        admin_email = os.getenv("ADMIN_EMAIL")
        admin_password = os.getenv("ADMIN_PASSWORD")
        
        if not admin_email or not admin_password:
            print("ADMIN_EMAIL and ADMIN_PASSWORD not set. Skipping admin seed.")
            return
        
        # Check if admin already exists
        result = await session.execute(select(User).where(User.email == admin_email.lower()))
        user = result.scalar_one_or_none()

        if user is None:
            hashed_password = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            user = User(email=admin_email.lower(), password=hashed_password)
            session.add(user)
            await session.commit()
            print(f"Admin user {admin_email} created.")
        else:
            print(f"Admin user {admin_email} already exists.")

if __name__ == "__main__":
    asyncio.run(seed_database())
