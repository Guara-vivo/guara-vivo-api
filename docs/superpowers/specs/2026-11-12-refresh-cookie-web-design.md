# Token de renovação do painel em cookie HttpOnly (sessão web separada da mobile)

Issue: Guara-vivo/documents#55. Repositórios: `guara-vivo-api` (maior parte) e `guara-vivo-web`.

## Intenção

Hoje o painel guarda o token de renovação em `localStorage` (com "Manter conectado") ou `sessionStorage`. Quem consegue rodar script na página (XSS) lê esse token e o leva embora; ele vale 1 ano. O objetivo é que o JavaScript do painel **nunca** veja o token de renovação, e que a sessão do painel seja mais curta que a do app.

Isso só ficou possível depois da #49: painel (`guaravivo.com.br`) e API (`api.guaravivo.com.br`) agora são do mesmo site, então um cookie `SameSite=Lax` funciona.

Ganho real, sem prometer demais: com um XSS, o atacante ainda age como a pessoa enquanto a página estiver aberta (o navegador manda o cookie nas chamadas dele). Ele deixa de conseguir **levar a sessão** e usá-la de outra máquina por dias.

## Decisões já tomadas (pelo usuário)

- **Duas sessões:** mobile mantém 1 ano (o app trabalha offline por meses); painel passa a 7 dias. Valor real hoje em produção: 1 ano para todos.
- O painel **para de usar** `localStorage`/`sessionStorage` para o token de renovação; o token vive só no cookie.
- Para o painel, a API **não devolve** o token de renovação no corpo (senão um script chamaria `/users/refresh` e leria a resposta).
- O app mobile continua exatamente como está, sem nenhuma alteração.

## Metas

- Nenhum token de renovação em `localStorage`/`sessionStorage` depois da transição.
- Login, renovação, logout, "Manter conectado" e troca de senha funcionam no painel (Chrome, Firefox e Safari; quem testa nos navegadores é o usuário).
- Ninguém é deslogado por causa do deploy.
- Teste cobrindo a recusa de renovação por cookie com `Origin` não permitido.

## Não metas / anti-metas

- Não mudar o token de acesso (1 hora, em memória no painel) nem o WebSocket de andamento.
- Não criar token anti-CSRF separado.
- Não remover o caminho pelo corpo da requisição (o mobile depende dele).
- Não resolver o caso de várias abas renovando ao mesmo tempo: a rotação só tolera uma renovação por vez, e hoje já é assim (o `localStorage` também era compartilhado entre abas). O painel continua usando uma única renovação em andamento por aba.
- Falha se: o app mobile deixar de funcionar, alguém for deslogado no deploy, ou o `refresh_token` do painel aparecer no corpo de uma resposta.

## Como funciona

### Tipo de sessão (API)

Nova coluna `refresh_tokens.session_kind` (migração aditiva, `NOT NULL`, padrão `'mobile'`, então todos os tokens existentes continuam mobile):

| `session_kind` | Quem | Validade (deslizante) | Entrega |
| --- | --- | --- | --- |
| `mobile` | app e qualquer cliente sem `client` | `JWT_REFRESH_TOKEN_EXPIRE` (1 ano, como hoje) | corpo da resposta |
| `web` | painel com "Manter conectado" | `JWT_WEB_REFRESH_TOKEN_EXPIRE` (padrão 604800 = 7 dias) | cookie persistente (`Max-Age`) |
| `web_session` | painel sem "Manter conectado" | `JWT_WEB_SESSION_REFRESH_TOKEN_EXPIRE` (padrão 86400 = 24 h) | cookie de sessão (sem `Max-Age`) |

"Deslizante": cada renovação emite um token novo e ganha o prazo cheio de novo. A renovação **herda** o `session_kind` do token que está sendo trocado. O formato da resposta (cookie ou corpo) segue o `session_kind` do token **novo**.

As duas variáveis novas vão em `docker-compose.yml` com padrão (`${VAR:-valor}`), para chegarem ao container sem novo secret.

### Endpoints

- `POST /users/login`: o corpo ganha `client` (`"web"` | `"mobile"`, padrão `"mobile"`) e `remember` (bool, padrão `false`, só vale com `client="web"`). Com `client="web"`: cria o token com `web`/`web_session`, responde `Set-Cookie` e um corpo **sem** `refresh_token` (só `access_token`, `token_type`, `user`). Sem `client` ou `"mobile"`: igual a hoje.
- `POST /users/refresh`: o corpo `refresh_token` passa a ser **opcional**. Origem do token: corpo se vier, senão o cookie. Sem nenhum dos dois: 401. Rotação atômica e limite por IP como hoje.
  - **Migração do painel:** o painel manda o token antigo no corpo com `client="web"` e `remember` (true se estava no `localStorage`, false se no `sessionStorage`). Isso converte a sessão: o token novo sai como `web`/`web_session`, entregue por cookie. Só vale se o corpo trouxer o token (um cookie já é web e herda).
- `POST /users/logout`: aceita o token pelo corpo (opcional) ou pelo cookie. Revoga como hoje (continua devolvendo 200 mesmo se não achar) e, se havia cookie, responde `Set-Cookie` expirando-o (mesmos `Path`/atributos).

### Cookie

Nome `gv_refresh`; `HttpOnly`; `Secure` (controlado por `REFRESH_COOKIE_SECURE`, padrão `true`; o `.env.example` local usa `false`); `SameSite=Lax`; `Path=/users`; **sem `Domain`** (host-only em `api.guaravivo.com.br`, mais restrito que `Domain=guaravivo.com.br`). `Max-Age` só em `web`.

### CSRF

Quando o token vier **do cookie**, a API confere o `Origin`: se existir e não estiver em `CORS_ORIGINS`, responde 403. Sem `Origin` é aceito (navegadores sempre mandam `Origin` em POST; clientes sem `Origin` não são CSRF, e o proxy de desenvolvimento do Vite remove o cabeçalho). Token vindo do corpo não passa por essa checagem, então o mobile não é afetado. `SameSite=Lax` já impede o navegador de mandar o cookie em POST de outro site; o `Origin` cobre outro subdomínio do mesmo site.

O CORS já tem `allow_credentials=True` e `CORS_ORIGINS` é lista exata (hoje só `https://guaravivo.com.br`). `allow_headers` não precisa mudar.

### Painel (`guara-vivo-web`)

- `AuthContext.tsx` para de ler e escrever `gv.refresh`. Todas as chamadas a `/users/login|refresh|logout` usam `credentials: 'include'` (ajustar `apiFetch` em `src/lib/api.ts`). Login manda `client: 'web'` e `remember`.
- Marcador sem segredo `gv.sessao` no `localStorage`: `'1'` (lembrar) ou `'0'` (sessão). Serve para (a) só tentar `/users/refresh` ao abrir quando já houve login e (b) saber o `remember` na troca de senha, que revoga todos os tokens e refaz o login. O estado inicial `loading` passa a depender desse marcador, não do token.
- Transição: ao abrir, se existir `gv.refresh` antigo (em `localStorage` ou `sessionStorage`), manda uma vez no corpo de `/users/refresh` com `client: 'web'` e o `remember` correspondente, grava `gv.sessao`, e **apaga** o item antigo. Depois de um tempo esse bloco pode ser removido.
- Recusa de verdade (401/403) derruba a sessão local e limpa o marcador; servidor fora do ar não derruba (como hoje).
- Desenvolvimento local: o proxy do Vite (`/api`) reescreve o caminho, então o cookie `Path=/users` não casaria com `/api/users/...`. Adicionar `cookiePathRewrite: { '/users': '/api/users' }` no proxy e conferir que o `Secure` funciona em `localhost` (Chrome e Firefox aceitam; Safari não: usar `REFRESH_COOKIE_SECURE=false` na API local).

## Ordem de entrega e reversão

1. API primeiro (retrocompatível: o painel antigo continua funcionando, pois sem `client` tudo é mobile).
2. Painel depois (migra as sessões abertas sem deslogar).
3. Reversão: reverter o painel; a API fica (migração aditiva). Se precisar reverter a API, a coluna pode ficar.

A migração roda sozinha no `alembic upgrade head` do container.

## Testes (a lista mínima)

API: login web/remember (cookie persistente, sem `refresh_token` no corpo), login web sem remember (cookie de sessão), login sem `client` (corpo como hoje); renovação por cookie, por corpo e herança do tipo; migração por corpo com `client="web"`; recusa por `Origin` não permitido (403) e aceitação sem `Origin`; logout por cookie expira o cookie; sem token 401; rotação concorrente continua segura; tokens existentes seguem válidos como `mobile`. Painel: sem token em `localStorage`/`sessionStorage` após login; restauração ao abrir; transição do token antigo; troca de senha refaz o login.

## O que fica a cargo de quem constrói

- Nome exato dos campos Pydantic e como devolver o corpo sem `refresh_token` para web (resposta manual ou modelo separado).
- Montar o cookie (helper único para criar e expirar).
- Detalhes do `apiFetch` e dos testes do painel.

## Decisões minhas (marcar para o usuário conferir)

- 24 h de validade no servidor para a sessão sem "Manter conectado".
- Prazo de 7 dias deslizante (não absoluto).
- Cookie sem `Domain` e `Path=/users`; nome `gv_refresh`.
- Aceitar `Origin` ausente (só recusa se presente e não permitido).
- Aceitar `client`/`remember` informados pelo próprio cliente: ele só consegue, no máximo, escolher o prazo do **seu** token (o mobile já tem 1 ano), sem ganho de privilégio.
- Migrar sessão por corpo + `client="web"` em vez de manter o token antigo para sempre.
