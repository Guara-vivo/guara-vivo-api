# Token de renovação do painel em cookie HttpOnly: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O painel deixa de guardar o token de renovação no navegador: ele vive num cookie `HttpOnly`, com sessão web (7 dias) separada da mobile (1 ano).

**Architecture:** A API ganha um "tipo de sessão" por token de renovação (`mobile` | `web` | `web_session`), que define validade e forma de entrega (corpo ou cookie) e é herdado na rotação. Quem não manda `client` é mobile, então o app não muda. O painel passa a falar com `credentials: 'include'` e só guarda um marcador sem segredo.

**Tech Stack:** FastAPI + SQLModel + Alembic (Postgres em produção, SQLite nos testes HTTP), `unittest`; painel em React + Vite (sem framework de teste: verificação por `npm run build` e roteiro manual).

**Spec:** `docs/superpowers/specs/2026-11-12-refresh-cookie-web-design.md` (neste repositório). Issue: Guara-vivo/documents#55.

## Global Constraints

- Mobile: validade `JWT_REFRESH_TOKEN_EXPIRE` (1 ano), token no corpo, **nenhuma mudança de contrato** para quem não manda `client`.
- Painel: `web` = `JWT_WEB_REFRESH_TOKEN_EXPIRE` (padrão `604800`), `web_session` = `JWT_WEB_SESSION_REFRESH_TOKEN_EXPIRE` (padrão `86400`); validade deslizante; a renovação herda o `session_kind` do token trocado; o formato da resposta segue o `session_kind` do token **novo**.
- Cookie: nome `gv_refresh`, `HttpOnly`, `Secure` controlado por `REFRESH_COOKIE_SECURE` (padrão `true`), `SameSite=Lax`, `Path=/users`, **sem `Domain`**, `Max-Age` só em `web`.
- Para o painel (`web`/`web_session`) o `refresh_token` **nunca** aparece no corpo de uma resposta.
- `POST /users/refresh`: token do corpo se vier, senão do cookie; sem nenhum: 401. Corpo opcional. Migração do painel = corpo + `client="web"` + `remember`.
- CSRF: token vindo do **cookie** → se `Origin` existe e não está em `CORS_ORIGINS`, 403; sem `Origin` é aceito. Token do corpo não passa por essa checagem.
- Migração Alembic aditiva (`session_kind` `NOT NULL`, padrão `'mobile'`); segue o padrão dos arquivos existentes (só roda em PostgreSQL).
- Não mudar o token de acesso, o WebSocket, nem remover o caminho pelo corpo. Ninguém pode ser deslogado pelo deploy.
- Ordem: API primeiro (retrocompatível), painel depois. Commits em português ou inglês curto, com a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Entradas e falhas que o spec implica mas as tarefas normais não exercitam (cada uma tem teste na tarefa dona):

1. **Regressão do mobile:** login e refresh sem `client` continuam devolvendo `refresh_token` no corpo e nenhum `Set-Cookie` (Tarefas 3 e 4).
2. **Corpo e cookie juntos:** o corpo vence e o `session_kind` vem do token do corpo (Tarefa 4).
3. **`Origin` esquisito:** `Origin: null`, outro subdomínio do mesmo site e outra porta → 403 com cookie; sem `Origin` → aceito; o mesmo `Origin` ruim com token **no corpo** → aceito (Tarefa 4).
4. **Falhas não deixam cookie vivo:** login com senha errada não define cookie; refresh com cookie inválido/expirado/revogado dá 401 **e expira o cookie** (Tarefas 3 e 4).
5. **Troca de senha:** `PUT /users/{id}` com senha nova revoga também os tokens `web`/`web_session`, e o cookie antigo passa a dar 401 (Tarefa 4).
6. **Corpo `refresh_token` vazio (`""`)** é 422, não "ausente"; `null`/ausente é "ausente" (Tarefa 4).

## Estrutura de arquivos

API (`guara-vivo-api`):
- Criar `src/refresh_cookie.py`: cookie (criar/expirar) e checagem de `Origin`. Uma responsabilidade.
- Criar `migrations/versions/20261112_0019_add_refresh_token_session_kind.py`.
- Modificar `src/models/refresh_token.py`, `src/security.py`, `src/schemas.py`, `src/routes/user.py`, `src/main.py` (só se `get_cors_origins` precisar sair dali), `docker-compose.yml`, `.env.example`, `.env.docker-compose.example`, `AGENTS.md`.
- Criar `tests/test_session_kinds.py` (unitários, Tarefas 1 e 2) e `tests/test_refresh_cookie.py` (HTTP com SQLite, Tarefas 3 a 5, no estilo de `tests/test_nest_zone_visibility.py`).

Painel (`guara-vivo-web`): `src/lib/api.ts`, `src/auth/AuthContext.tsx`, `vite.config.ts`, `README.md`.

---

### Task 1: Tipo de sessão no token de renovação

**Files:**
- Create: `migrations/versions/20261112_0019_add_refresh_token_session_kind.py`
- Modify: `src/models/refresh_token.py`, `src/security.py`
- Test: `tests/test_session_kinds.py`

**Interfaces:**
- Produces (em `src/security.py`):
  - `SESSION_KIND_MOBILE = "mobile"`, `SESSION_KIND_WEB = "web"`, `SESSION_KIND_WEB_SESSION = "web_session"`
  - `WEB_SESSION_KINDS = (SESSION_KIND_WEB, SESSION_KIND_WEB_SESSION)`
  - `refresh_ttl_seconds(kind: str) -> int` (lê as três variáveis de ambiente no import; `ValueError` para tipo desconhecido)
  - `create_refresh_token(kind: str = SESSION_KIND_MOBILE) -> tuple[str, str, datetime]` (a validade passa a depender do tipo)
- Produces (em `RefreshToken`): coluna `session_kind: str`, padrão `"mobile"`.

- [ ] **Step 1: Escrever os testes que falham** em `tests/test_session_kinds.py`: `test_mobile_ttl_is_the_existing_refresh_setting` (== `security.JWT_REFRESH_TOKEN_EXPIRE`), `test_web_ttl_defaults_to_7_days` (604800), `test_web_session_ttl_defaults_to_24_hours` (86400), `test_unknown_kind_raises_value_error`, `test_create_refresh_token_expiry_follows_kind` (a diferença entre `expires_at` e agora fica dentro de 5 s do TTL do tipo, para os três tipos), `test_refresh_token_model_defaults_to_mobile` (`RefreshToken(...).session_kind == "mobile"`).
- [ ] **Step 2: Rodar e ver falhar:** `python -m unittest tests.test_session_kinds -v` → falha por nomes inexistentes.
- [ ] **Step 3: Implementar** as constantes, `refresh_ttl_seconds`, o parâmetro de `create_refresh_token` em `src/security.py`; a coluna em `src/models/refresh_token.py` (`String(16)`, `nullable=False`, `server_default="mobile"`); a migração `20261112_0019` (`down_revision = "20260610_0018"`, mesmo padrão de pular fora do PostgreSQL das outras, `add_column` com `server_default="mobile"`, `downgrade` com `drop_column`).
- [ ] **Step 4: Rodar e ver passar:** `python -m unittest tests.test_session_kinds -v` e a suíte (`python -m unittest discover -s tests`) → tudo OK.
- [ ] **Step 5: Commit** (`feat(auth): tipo de sessão em refresh_tokens`).

### Task 2: Cookie e checagem de Origin

**Files:**
- Create: `src/refresh_cookie.py`
- Modify: `src/main.py` (se `get_cors_origins` precisar sair dali para evitar import circular: `routes` não pode importar `main`)
- Test: `tests/test_session_kinds.py` (acrescentar classes)

**Interfaces:**
- Consumes: `SESSION_KIND_WEB`, `refresh_ttl_seconds` (Tarefa 1).
- Produces (em `src/refresh_cookie.py`):
  - `REFRESH_COOKIE_NAME = "gv_refresh"`, `REFRESH_COOKIE_PATH = "/users"`
  - `set_refresh_cookie(response: Response, token: str, kind: str) -> None` (`Max-Age` só para `web`)
  - `clear_refresh_cookie(response: Response) -> None` (mesmos `Path`/atributos, `Max-Age=0`)
  - `reject_foreign_origin(request: Request) -> None` (levanta `HTTPException(403)`; usa a lista de `CORS_ORIGINS`)
  - `get_cors_origins() -> list[str]` (movida de `main.py`; `main.py` passa a importá-la)

- [ ] **Step 1: Escrever os testes que falham:** `test_web_cookie_attributes` (o `Set-Cookie` tem `HttpOnly`, `SameSite=lax`, `Path=/users`, `Max-Age=604800`, `gv_refresh=<token>` e **não** tem `Domain`), `test_web_session_cookie_has_no_max_age`, `test_secure_flag_follows_env` (com `REFRESH_COOKIE_SECURE=false` não há `Secure`; sem a variável há), `test_clear_cookie_expires_it` (`Max-Age=0`, mesmo `Path`), `test_origin_allowed_passes`, `test_origin_missing_passes`, `test_origin_foreign_is_403` (para `https://evil.example`, `https://api.guaravivo.com.br`, `http://guaravivo.com.br`, `https://guaravivo.com.br:8443` e `null`, com `CORS_ORIGINS=https://guaravivo.com.br`).
- [ ] **Step 2: Rodar e ver falhar** (`python -m unittest tests.test_session_kinds -v`).
- [ ] **Step 3: Implementar** `src/refresh_cookie.py` com `Response.set_cookie`/`delete_cookie` do Starlette (`delete_cookie` com os mesmos `path`, `secure`, `httponly`, `samesite`), e mover `get_cors_origins`.
- [ ] **Step 4: Rodar e ver passar** (inclui a suíte inteira: o CORS do `main.py` não pode mudar de comportamento).
- [ ] **Step 5: Commit** (`feat(auth): cookie de renovação e checagem de Origin`).

### Task 3: Login do painel

**Files:**
- Modify: `src/schemas.py`, `src/routes/user.py` (`create_db_refresh_token`, `login`)
- Test: `tests/test_refresh_cookie.py` (criar, com o arcabouço SQLite)

**Interfaces:**
- Consumes: Tarefas 1 e 2.
- Produces:
  - `UserLogin.client: Literal["web", "mobile"] = "mobile"` e `UserLogin.remember: bool = False`
  - `WebToken(SQLModel)`: `access_token: str`, `token_type: str = "bearer"`, `user: UserRead` (sem `refresh_token`)
  - `create_db_refresh_token(db, user, request, session_kind: str = SESSION_KIND_MOBILE) -> tuple[str, RefreshToken]`, gravando `session_kind` e a validade do tipo
  - Arcabouço de teste reutilizado nas Tarefas 4 e 5: usuários semeados com senha bcrypt real (`hash_password`), `TestClient(main.app)` com `get_db` sobrescrito, e as tabelas `User` e `RefreshToken` (sem colunas array, então não precisa trocar tipos por JSON), `CORS_ORIGINS=https://guaravivo.com.br` no ambiente do teste.

- [ ] **Step 1: Escrever os testes que falham** (cada um faz `POST /users/login`): `test_web_login_with_remember_sets_persistent_cookie_and_no_refresh_token_in_body` (200; `Set-Cookie` com `gv_refresh`, `HttpOnly`, `Max-Age=604800`; `"refresh_token" not in response.json()`; `access_token` presente; linha no banco com `session_kind == "web"`), `test_web_login_without_remember_sets_session_cookie` (sem `Max-Age`; `session_kind == "web_session"`, `expires_at` ≈ 24 h), `test_mobile_login_unchanged` (sem `client`: `refresh_token` no corpo, **sem** `Set-Cookie`, `session_kind == "mobile"`, validade ≈ 1 ano), `test_failed_login_sets_no_cookie` (401 sem `Set-Cookie`), `test_invalid_client_value_is_422`.
- [ ] **Step 2: Rodar e ver falhar:** `python -m unittest tests.test_refresh_cookie -v`.
- [ ] **Step 3: Implementar** os campos e `WebToken`; em `login`, quando `client == "web"`, escolher `web` se `remember` senão `web_session`, criar o token e devolver um `JSONResponse` com `WebToken` e o cookie (`set_refresh_cookie`); caso contrário, o caminho de hoje. Contornar o `response_model=Token` com a resposta explícita.
- [ ] **Step 4: Rodar e ver passar** (arquivo novo e suíte inteira).
- [ ] **Step 5: Commit** (`feat(auth): login do painel por cookie`).

### Task 4: Renovação (cookie, corpo, herança, migração, CSRF)

**Files:**
- Modify: `src/schemas.py` (`RefreshTokenRequest`), `src/routes/user.py` (`validate_and_rotate_refresh_token_atomically`, `refresh_token`)
- Test: `tests/test_refresh_cookie.py`

**Interfaces:**
- Consumes: Tarefas 1 a 3 (inclusive o arcabouço de teste).
- Produces:
  - `RefreshTokenRequest`: `refresh_token: Optional[str] = Field(default=None, min_length=1)`, `client: Optional[Literal["web","mobile"]] = None`, `remember: bool = False`; o corpo inteiro da rota é opcional.
  - `validate_and_rotate_refresh_token_atomically(db, refresh_token: str, request, new_kind: str | None = None) -> tuple[User, str, str, str]` = `(user, access_token, new_refresh_token, new_kind)`; sem `new_kind`, herda o do token trocado.

- [ ] **Step 1: Escrever os testes que falham** (`POST /users/refresh`): `test_refresh_by_cookie_rotates_and_resets_cookie` (login web, refresh só com o cookie: 200, novo `Set-Cookie`, sem `refresh_token` no corpo, cookie novo ≠ antigo; o antigo, reusado, dá 401), `test_refresh_inherits_session_kind` (web continua `web`; web_session continua `web_session`; mobile continua `mobile` com token no corpo e sem cookie), `test_mobile_refresh_by_body_unchanged` (foco 1), `test_body_wins_over_cookie` (foco 2: cookie de um usuário, corpo de outro → renova o do corpo), `test_body_token_with_client_web_migrates_to_cookie` (token mobile no corpo + `client:"web"` + `remember:true` → `session_kind` novo `web`, cookie definido, sem `refresh_token` no corpo; com `remember:false` → `web_session`), `test_foreign_origin_with_cookie_is_403_and_does_not_rotate` (foco 3: cada `Origin` ruim da Tarefa 2; o token continua válido depois), `test_missing_origin_with_cookie_is_accepted`, `test_foreign_origin_with_body_token_is_accepted` (foco 3), `test_invalid_cookie_is_401_and_expires_cookie` (foco 4: desconhecido, revogado e expirado), `test_no_token_at_all_is_401`, `test_empty_body_refresh_token_is_422` (foco 6), `test_password_change_revokes_web_tokens` (foco 5: `PUT /users/{id}` com `password` novo e `current_password` certo, depois o cookie antigo dá 401).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar:** origem do token (corpo senão cookie; `reject_foreign_origin` **só** quando veio do cookie); `new_kind` = `web`/`web_session` conforme `remember` apenas quando o corpo traz token **e** `client == "web"`, senão herdado; resposta por `new_kind` (web: cookie e `WebToken`; mobile: `Token` como hoje); em 401 por token inválido vindo do cookie, devolver o 401 **com** `clear_refresh_cookie`; manter o limite por IP e a rotação atômica.
- [ ] **Step 4: Rodar e ver passar** (arquivo e suíte inteira).
- [ ] **Step 5: Commit** (`feat(auth): renovação por cookie com herança do tipo e checagem de Origin`).

### Task 5: Logout

**Files:**
- Modify: `src/schemas.py` (`LogoutRequest.refresh_token` opcional), `src/routes/user.py` (`logout`)
- Test: `tests/test_refresh_cookie.py`

**Interfaces:**
- Consumes: Tarefas 2 a 4.

- [ ] **Step 1: Escrever os testes que falham** (`POST /users/logout`): `test_logout_by_cookie_revokes_and_expires_cookie` (200; `Set-Cookie` com `Max-Age=0`; o token revogado dá 401 no refresh), `test_logout_by_body_still_works` (mobile), `test_logout_without_any_token_is_200` e `test_logout_with_unknown_token_is_200` (como hoje), `test_logout_with_cookie_and_foreign_origin_is_403` (a mesma regra de CSRF; o token continua válido).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar:** token do corpo senão do cookie; checagem de `Origin` só para cookie; revogar como hoje; se havia cookie, resposta com `clear_refresh_cookie`.
- [ ] **Step 4: Rodar e ver passar** (arquivo e suíte inteira).
- [ ] **Step 5: Commit** (`feat(auth): logout por cookie`).

### Task 6: Configuração, documentação e PR da API

**Files:**
- Modify: `docker-compose.yml`, `.env.example`, `.env.docker-compose.example`, `AGENTS.md`

- [ ] **Step 1:** Em `docker-compose.yml` (serviço `api`, junto das outras variáveis JWT) acrescentar `JWT_WEB_REFRESH_TOKEN_EXPIRE: ${JWT_WEB_REFRESH_TOKEN_EXPIRE:-604800}`, `JWT_WEB_SESSION_REFRESH_TOKEN_EXPIRE: ${JWT_WEB_SESSION_REFRESH_TOKEN_EXPIRE:-86400}` e `REFRESH_COOKIE_SECURE: ${REFRESH_COOKIE_SECURE:-true}`. Nos dois `.env*.example`, as três variáveis comentadas, com `REFRESH_COOKIE_SECURE=false` indicado para desenvolvimento local em HTTP. Em `AGENTS.md`, atualizar as seções "Authentication" e "Environment" (tipos de sessão, cookie, `client`/`remember`, regra de `Origin`).
- [ ] **Step 2: Verificar:** `python -m unittest discover -s tests` → tudo OK; `python -m unittest tests.test_forwarded_ips` continua passando (o teste lê o `docker-compose.yml`).
- [ ] **Step 3: Commit, push e PR** em `Guara-vivo/guara-vivo-api` (corpo com resumo, ordem de entrega e a lista de verificação pós-deploy abaixo). **Não mergear sem o "pode" explícito do usuário**; o merge dispara o deploy.
- [ ] **Step 4: Verificação pós-deploy** (o usuário fornece uma conta de teste na hora; a senha nunca é gravada nem commitada): `curl -si -X POST https://api.guaravivo.com.br/users/login -H 'Content-Type: application/json' -d '{"email":"...","password":"...","client":"web","remember":true}'` → `Set-Cookie: gv_refresh=...; HttpOnly; Secure; SameSite=lax; Path=/users; Max-Age=604800` e corpo **sem** `refresh_token`; o mesmo login sem `client` → `refresh_token` no corpo e sem `Set-Cookie`; `POST /users/refresh` com `-b` do cookie e `-H 'Origin: https://evil.example'` → 403, e com `-H 'Origin: https://guaravivo.com.br'` → 200. O painel antigo (ainda em produção) continua logando e renovando.

### Task 7: Painel: cliente HTTP e proxy de desenvolvimento

**Files:**
- Modify: `guara-vivo-web/src/lib/api.ts`, `guara-vivo-web/vite.config.ts`
- (Pré-condição: a API da Tarefa 6 está em produção. Branch nova a partir de `origin/main`.)

**Interfaces:**
- Produces: `RequestOptions.credentials?: RequestCredentials`, repassado ao `fetch` em `apiFetch` (padrão do `fetch`, sem forçar `include` nas chamadas que não são de autenticação).

- [ ] **Step 1:** Acrescentar `credentials` a `RequestOptions` e ao `fetch` em `apiFetch`.
- [ ] **Step 2:** No proxy `/api` do `vite.config.ts` acrescentar `cookiePathRewrite: { '/users': '/api/users' }` (o cookie é `Path=/users` e o navegador, em desenvolvimento, vê `/api/users/...`).
- [ ] **Step 3: Verificar:** `npm run build` → termina sem erro.
- [ ] **Step 4: Commit** (`feat(auth): credentials nas chamadas e cookiePathRewrite no proxy`).

### Task 8: Painel: sessão sem token no navegador

**Files:**
- Modify: `guara-vivo-web/src/auth/AuthContext.tsx`, `guara-vivo-web/README.md`

**Interfaces:**
- Consumes: `credentials` de `apiFetch` (Tarefa 7); contrato da API das Tarefas 3 a 5.
- Produces (internas ao arquivo): `SESSION_MARKER_KEY = 'gv.sessao'` (`'1'` lembrar, `'0'` sessão), `LEGACY_REFRESH_KEY = 'gv.refresh'`; `readMarker(): boolean | null` (`true` = lembrar), `writeMarker(remember: boolean): void`, `clearMarker(): void`, `readLegacyRefresh(): { token: string; remember: boolean } | null`, `clearLegacyRefresh(): void`. `TokenResponse` perde `refresh_token`.

- [ ] **Step 1:** Trocar `readRefresh`/`writeRefresh`/`clearRefresh` pelas funções acima (marcador no `localStorage`; o legado lido de `localStorage` → `remember: true` e de `sessionStorage` → `remember: false`). O estado inicial `loading` vale quando há marcador **ou** token legado.
- [ ] **Step 2:** `login` manda `{ email, password, client: 'web', remember }` com `credentials: 'include'` e grava o marcador. `refresh` manda `/users/refresh` com `credentials: 'include'`: com legado, o corpo é `{ refresh_token, client: 'web', remember }`; sem legado, `{}`. Só apaga o legado quando a API aceita (2xx) **ou** recusa (4xx); com rede fora do ar ou 5xx o legado fica para a próxima tentativa. `logout` manda `{}` (e o legado, se ainda existir) com `credentials: 'include'`, depois limpa marcador e legado. Recusa de verdade (4xx) em `refresh` limpa o marcador; rede/5xx não derruba, como hoje. `updateAccount` usa o marcador (não o token) para o `remember` do novo login.
- [ ] **Step 3:** Atualizar o `README.md` onde descreve que o token fica em `localStorage`/`sessionStorage` (comentário de `AuthContext.tsx` incluído).
- [ ] **Step 4: Verificar:** `npm run build` → OK. Roteiro manual em `npm run dev` (Chrome; Firefox e Safari ficam com o usuário), contra a API em produção via proxy:
  1. Login **com** "Manter conectado": DevTools → Application: nenhum `gv.refresh`; cookie `gv_refresh` com `HttpOnly` e expiração em ~7 dias; recarregar mantém logado.
  2. Login **sem** marcar: cookie de sessão (sem expiração); recarregar mantém; fechar e reabrir o navegador pede login.
  3. Logout: cookie some e recarregar volta à tela de login.
  4. Troca de senha em "conta": a sessão continua (novo login automático).
  5. Migração: obter um token mobile por `curl` (login sem `client`), gravá-lo em `localStorage['gv.refresh']`, abrir o painel → entra logado, `gv.refresh` desaparece, aparece o cookie.
  6. Andamento ao vivo (WebSocket) continua funcionando.
- [ ] **Step 5: Commit, push e PR** em `Guara-vivo/guara-vivo-web`. **Não mergear sem o "pode" explícito do usuário.**

### Task 9: Fechamento

- [ ] Depois do merge do painel: conferir em produção (login, recarregar, logout) e o `Set-Cookie` pelo navegador; o usuário testa Firefox e Safari.
- [ ] Postar o andamento na #55 (texto aprovado pelo usuário antes), mover o card para `Done` e fechar.
- [ ] Anotar numa issue nova (só se o usuário quiser) a remoção do bloco de migração do painel (`gv.refresh`) depois de algumas semanas.
