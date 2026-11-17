"""Mede quanto um login (bcrypt) atrasa outras requisições do mesmo processo.

Sobe o app em memória (ASGI, banco SQLite descartável, nada de rede nem de Postgres), dispara
logins concorrentes e, ao mesmo tempo, faz GET /health a cada 5 ms, anotando a latência de cada
/health. Se o bcrypt roda no event loop, o /health fica preso durante todo o hash.

    python scripts/bench_login_event_loop.py                 # código deste checkout
    python scripts/bench_login_event_loop.py --src <outro>/src   # compara com outro checkout

Só lê e escreve num arquivo temporário. Não usa DATABASE_URL real.
"""
import argparse
import asyncio
import os
import shutil
import statistics
import sys
import tempfile
import time
import types
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
parser.add_argument("--logins", type=int, default=4, help="logins concorrentes por rodada (máx. 5, limite por IP)")
parser.add_argument("--rounds", type=int, default=5)
args = parser.parse_args()
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, args.src)
os.environ["DATABASE_URL"] = "postgres://user:password@localhost:5432/db"  # só para o import; nunca conecta
os.environ.setdefault("JWT_SECRET_KEY", "bench-only-secret-with-32-bytes-min!!")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")
magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _b, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

import httpx  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import main  # noqa: E402
from database import get_db  # noqa: E402
from models import RefreshToken, User  # noqa: E402
from routes import user as user_routes  # noqa: E402

EMAIL, PASSWORD = "bench@example.com", "bench-correct-horse"


def ms(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))] * 1000


async def probe_health(client: httpx.AsyncClient, stop: asyncio.Event, out: list[float]) -> None:
    while not stop.is_set():
        started = time.perf_counter()
        response = await client.get("/health")
        out.append(time.perf_counter() - started)
        assert response.status_code == 200
        await asyncio.sleep(0.005)


async def run(client: httpx.AsyncClient) -> None:
    await client.get("/health")  # warm up

    idle: list[float] = []
    stop = asyncio.Event()
    task = asyncio.create_task(probe_health(client, stop, idle))
    await asyncio.sleep(1.0)
    stop.set()
    await task

    during: list[float] = []
    login_times: list[float] = []
    for _ in range(args.rounds):
        for limiter in (user_routes.login_ip_limiter, user_routes.login_email_limiter):
            limiter._hits.clear()
        stop = asyncio.Event()
        task = asyncio.create_task(probe_health(client, stop, during))
        await asyncio.sleep(0.05)

        async def login():
            started = time.perf_counter()
            response = await client.post("/users/login", json={"email": EMAIL, "password": PASSWORD})
            assert response.status_code == 200, response.text
            login_times.append(time.perf_counter() - started)

        await asyncio.gather(*(login() for _ in range(args.logins)))
        stop.set()
        await task

    print(f"código: {args.src}")
    print(f"logins concorrentes por rodada: {args.logins}, rodadas: {args.rounds}")
    print(f"login (tempo total por requisição)  mediana {statistics.median(login_times) * 1000:7.0f} ms")
    for label, data in (("/health sem login rodando", idle), ("/health durante logins     ", during)):
        print(
            f"{label}  n={len(data):4d}  p50 {ms(data, 0.5):7.1f} ms  p95 {ms(data, 0.95):7.1f} ms  max {max(data) * 1000:7.1f} ms"
        )


def main_bench() -> None:
    tmpdir = tempfile.mkdtemp(prefix="bench-login-")
    try:
        db_file = Path(tmpdir) / "bench.db"
        sync_engine = create_engine(f"sqlite:///{db_file}")
        SQLModel.metadata.create_all(sync_engine, tables=[User.__table__, RefreshToken.__table__])
        with Session(sync_engine) as session:
            session.add(User(id=1, name="Bench", email=EMAIL, password=user_routes.hash_password(PASSWORD)))
            session.commit()
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)
        sessionmaker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

        async def override_get_db():
            async with sessionmaker() as session:
                yield session

        main.app.dependency_overrides[get_db] = override_get_db

        async def go():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
                await run(client)
            await async_engine.dispose()

        asyncio.run(go())
        sync_engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main_bench()
