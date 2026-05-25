# Guara Vivo API

API FastAPI para cadastro de usuários, registros de observação, análises e íbis. O projeto usa PostgreSQL, SQLAlchemy async, SQLModel, Alembic, JWT e bcrypt.

## Stack

- Python 3
- FastAPI
- SQLModel / SQLAlchemy async
- PostgreSQL
- Alembic
- JWT com `python-jose`
- Senhas com `bcrypt`
- Validação com Pydantic e `email-validator`

## Requisitos

- Python instalado
- PostgreSQL disponível
- `DATABASE_URL` apontando para PostgreSQL
- `JWT_SECRET_KEY` configurado antes de usar login/JWT

SQLite não é suportado por padrão.

## Configuração

Crie e ative o ambiente virtual:

```bash
python -m venv venv
.\venv\Scripts\activate
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

Crie o arquivo `.env`:

```bash
copy .env.example .env
```

Configure as variáveis:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/mydatabase
JWT_SECRET_KEY=replace-with-a-secure-random-secret
JWT_ACCESS_TOKEN_EXPIRE=3600
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
MAX_REQUEST_BODY_BYTES=1048576
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT=30
DATABASE_POOL_RECYCLE=1800
```

Para gerar um secret seguro:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Banco De Dados

Aplicar migrations:

```bash
alembic upgrade head
```

Ver migration atual:

```bash
alembic current
```

O head atual esperado é `20260520_0008`.

O app não cria tabelas automaticamente no startup. Toda alteração persistente de schema deve ser feita via Alembic.

## Execução

Rodar em modo local:

```bash
python src/main.py
```

Ou com Uvicorn:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload
```

URL padrão:

```text
http://localhost:8001
```

Swagger:

```text
http://localhost:8001/docs
```

## Usuário Admin Inicial

No startup, se as variáveis `ADMIN_EMAIL` e `ADMIN_PASSWORD` forem configuradas e nenhum admin existir, a API cria um usuário admin automaticamente:

```env
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=replace-with-secure-password
```

Se essas variáveis não forem definidas, nenhum admin é criado no startup. O primeiro usuário deve ser criado manualmente via SQL ou via API após uma migração manual.

A senha é sempre armazenada como hash bcrypt, nunca em texto plano.

## Autenticação

Login:

```http
POST /users/login
```

Body:

```json
{
  "email": "admin@example.com",
  "password": "your-configured-password"
}
```

Resposta:

```json
{
  "access_token": "jwt-token",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "name": "admin",
    "email": "admin@example.com"
  }
}
```

Use o token em rotas protegidas:

```http
Authorization: Bearer <access_token>
```

Validar sessão atual:

```http
GET /users/me
```

Tokens expiram em 1 hora por padrão (`JWT_ACCESS_TOKEN_EXPIRE=3600`).

## Endpoints

### Users

- `POST /users/` cria usuário com senha obrigatória.
- `POST /users/login` autentica e retorna JWT.
- `GET /users/me` retorna usuário autenticado.
- `GET /users/{id}` exige JWT e só permite acessar o próprio usuário.
- `PUT /users/{id}` exige JWT e só permite atualizar o próprio usuário.
- `DELETE /users/{id}` exige JWT e só permite remover o próprio usuário.

### Records

- `GET /records/` lista registros com paginação.
- `GET /records/{id}` retorna um registro.
- `POST /records/` exige JWT e `user_id` igual ao usuário autenticado.
- `PUT /records/{id}` exige JWT e ownership.
- `DELETE /records/{id}` exige JWT e ownership.

### Analysis

- `GET /analysis/` lista análises com paginação.
- `GET /analysis/{id}` retorna uma análise.
- `POST /analysis/` exige JWT e ownership do record relacionado.
- `PUT /analysis/{id}` exige JWT e ownership do record relacionado.
- `DELETE /analysis/{id}` exige JWT e ownership do record relacionado.

### Ibis

- `GET /ibis/` lista íbis com paginação.
- `GET /ibis/{id}` retorna um íbis.
- `POST /ibis/` exige JWT e ownership pela análise/record relacionado.
- `PUT /ibis/{id}` exige JWT e ownership pela análise/record relacionado.
- `DELETE /ibis/{id}` exige JWT e ownership pela análise/record relacionado.

## Paginação

List endpoints aceitam:

```text
skip=0
limit=100
```

Limites:

- `skip >= 0`
- `1 <= limit <= 100`

Exemplo:

```http
GET /records/?skip=0&limit=50
```

## Segurança E Hardening

- Senhas são obrigatórias e armazenadas somente com bcrypt.
- `UserRead` não expõe `password`.
- Emails são validados com `EmailStr`, normalizados para lowercase e únicos no banco.
- Login tem rate limit simples em memória: 5 tentativas por IP a cada 60 segundos.
- CORS só é habilitado quando `CORS_ORIGINS` é configurado.
- Requests `POST`, `PUT` e `PATCH` com body devem usar `Content-Type: application/json`.
- Payloads acima de `MAX_REQUEST_BODY_BYTES` (default 10MB) são rejeitados pelo middleware ASGI.
- Rotas de escrita validam ownership antes de alterar dados relacionados.
- `JWT_SECRET_KEY` é obrigatório para emitir e validar tokens.
- Refresh tokens são single-use: validação, revogação e emissão ocorrem atomicamente com lock pessimista.
- Upload de imagens é validado por assinatura binária (magic bytes), não apenas pelo header `Content-Type`.
- Uploads suportam apenas JPEG, PNG e WebP.
- Limite agregado de upload: 100MB por request.
- Acesso a recursos de usuário não existentes ou não autorizados retorna `404` (não `403`), prevenindo enumeração.

Observação: o rate limit atual é por processo e em memória. Antes de escalar horizontalmente, substitua por Redis ou outro storage compartilhado.

## Performance

- SQLAlchemy async usa `pool_pre_ping=True`.
- Pool do banco é configurável por env.
- Índices atuais cobrem `users.email`, `records.user_id` e `ibis.analysis_id`.
- Listagens têm paginação limitada.
- Startup faz apenas seed mínimo do admin, sem criar dados de exemplo automaticamente.
- Upload é processado sequencialmente por arquivo para reduzir pico de memória.

## Migrations Atuais

Cadeia atual (head: `20260520_0008`):

```text
20260516_0001 -> 20260517_0002 -> d3a87201af95 -> 269cbb5d99ef -> 20260517_0003
    -> 20260517_0004 -> 20260517_0005 -> 20260518_0006 -> 20260518_0007
    -> 20260520_0008
```

Resumo:

- `20260516_0001_initial_schema.py` cria schema inicial.
- `20260517_0002_remove_analysis_unused_fields.py` remove campos não usados de `analyses`.
- `d3a87201af95_add_status_column_to_records_table.py` adiciona `records.status` (idempotent).
- `269cbb5d99ef_add_password_column_to_users_table.py` adiciona `users.password`.
- `20260517_0003_add_security_performance_indexes.py` adiciona índices de segurança/performance.
- `20260517_0004_fix_records_images_array_type.py` corrige `records.images` para `varchar[]`.
- `20260517_0005_add_unique_constraint_to_analyses_recorder_id.py` adiciona constraint único em `analyses.recorder_id`.
- `20260518_0006_make_datetimes_timezone_aware.py` converte datetime columns para timezone-aware (UTC).
- `20260518_0007_add_refresh_tokens.py` cria tabela `refresh_tokens` para JWT rotation.
- `20260520_0008_add_per_image_analysis.py` cria `analysis_images` e adiciona `ibis.analysis_image_id`.

## Testes E Verificação

Ainda não há suíte de testes no repositório.

Verificar sintaxe/imports básicos:

```bash
python -m compileall src migrations
```

Verificar Alembic:

```bash
alembic heads
alembic current
```

## Observações De Desenvolvimento

- Não commitar `.env` nem credenciais reais.
- Não habilitar `echo=True` no SQLAlchemy em execução normal.
- Não reintroduzir SQLite como fallback sem decisão explícita.
- Não usar `SQLModel.metadata.create_all()` no startup.
- Toda alteração de schema deve ter migration Alembic.
