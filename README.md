# Guará Vivo API

API REST (FastAPI) do Guará Vivo. Cuida de cadastro e login de usuários, registros de avistamento (fotos + local), zonas do mapa e dos resultados da análise de IA.

Quando o app cria um registro, a API salva as fotos no Supabase Storage, grava o registro no PostgreSQL e publica o `record_id` numa fila RabbitMQ. O [worker](https://github.com/Guara-vivo/guara-vivo-worker) consome a fila, chama o [identifier](https://github.com/Guara-vivo/guara-vivo-identifier) e grava o resultado de volta no banco.

```
app mobile ──> API (:8001) ──> RabbitMQ ──> worker ──> identifier (:8000)
                  │                           │
                  └────── PostgreSQL (Supabase) <────┘
```

**Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy async, PostgreSQL (Supabase), Alembic, JWT (PyJWT) + bcrypt, RabbitMQ (pika).

## O que você precisa antes

- Python 3.12 (ou Docker, se for subir a stack completa)
- Um banco **PostgreSQL** (hoje o time usa o Supabase; SQLite não é suportado)
- Para enviar fotos: projeto Supabase com o bucket de Storage `frontend-uploads`
- Para criar registros: um RabbitMQ acessível (veja abaixo)

## Opção 1: stack completa com Docker Compose

Sobe API, identifier, worker e RabbitMQ juntos. Os três repositórios precisam estar **lado a lado**, na mesma pasta:

```
guara-vivo/
├── guara-vivo-api/
├── guara-vivo-identifier/
└── guara-vivo-worker/
```

```bash
cd guara-vivo-api
cp .env.docker-compose.example .env.docker-compose   # no PowerShell: copy
# edite .env.docker-compose: DATABASE_URL, JWT_SECRET_KEY, chaves do Supabase, senha do RabbitMQ...
docker compose --env-file .env.docker-compose up --build
```

- API: http://localhost:8001 (documentação interativa em `/docs`)
- RabbitMQ (painel): http://localhost:15672
- As migrations rodam sozinhas antes da API subir.
- O banco **não** sobe no Compose: use a `DATABASE_URL` do Supabase.
- O build do identifier copia o arquivo `yolo11x-seg.pt`, que não está no Git. Coloque-o na raiz de `guara-vivo-identifier/` antes do build (veja o README dele).

## Opção 2: só a API, em Python

Bom para mexer no código da API. Para criar registros você ainda precisa de um RabbitMQ; o mais simples é um container:

```bash
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3.13-management
```

Depois:

```powershell
python -m venv venv
.\venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt

copy .env.example .env           # Linux/macOS: cp
```

Edite o `.env`. O mínimo para rodar é:

| Variável | Para quê |
| --- | --- |
| `DATABASE_URL` | Conexão PostgreSQL (`postgresql://usuario:senha@host:5432/banco`) |
| `JWT_SECRET_KEY` | Segredo dos tokens. Gere com `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `RABBITMQ_HOST` | Use `localhost` ao rodar fora do Docker (o exemplo vem com `rabbitmq`) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Opcionais: criam o usuário admin na primeira subida (os dois juntos) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Necessárias só para upload de fotos |

Aplique as migrations e suba a API:

```powershell
alembic upgrade head
python src/main.py
# ou, com reload automático:
uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload
```

(`python src/main.py` já escuta em `0.0.0.0`, o que permite que um celular na mesma rede acesse a API.)

Pronto: http://localhost:8001/docs (a documentação some se `APP_ENV=production`). Para checar que está de pé: `GET /health`.

## Testes

Não precisam de banco nem de RabbitMQ de verdade.

```powershell
pip install pytest
$env:DATABASE_URL = "postgresql://user:password@localhost:5432/test"   # Linux/macOS: export
$env:JWT_SECRET_KEY = "teste-local"
python -m pytest -q
```

## Estrutura

```
src/          código da API (routes/, models/, services/, schemas.py, main.py)
migrations/   migrations do Alembic
tests/        testes (pytest)
docs/         documentação de referência
```

Mudou um model? Gere a migration com `alembic revision --autogenerate -m "descricao"`, revise o arquivo gerado e aplique com `alembic upgrade head`.

## Mais detalhes

Endpoints, autenticação, paginação, segurança, performance e histórico de migrations estão em [`docs/REFERENCIA.md`](docs/REFERENCIA.md).
