# Monitoramento e alertas

Como saber se a API e o servidor estão bem, onde ver e o que fazer quando um alerta chega. Foi montado na issue [Guara-vivo/documents#61](https://github.com/Guara-vivo/documents/issues/61). Última revisão: 2026-11-12.

## Visão geral

Duas camadas, uma de fora e uma de dentro do servidor:

| Camada | Ferramenta | O que vigia | Como avisa |
|---|---|---|---|
| De fora | UptimeRobot (plano grátis) | `https://api.guaravivo.com.br/health` e `https://guaravivo.com.br`, a cada 5 minutos | E-mail |
| De dentro | Netdata Cloud (plano grátis) | CPU, memória, disco, contêineres e se o servidor some do Cloud | E-mail (Critical, Warning e Unreachable) |

O UptimeRobot responde "a API está de pé para quem usa?". O Netdata responde "o servidor está saudável por dentro?". Um não substitui o outro.

## Onde ver

- **UptimeRobot:** painel em uptimerobot.com, na conta da equipe. Mostra os dois monitores, o histórico de quedas e o tempo de resposta.
- **Netdata Cloud:** app.netdata.cloud, no Space do projeto, nó `ip-172-31-19-63`. Abre pelo navegador, **sem SSH**.
- **Painel local do Netdata** (só se o Cloud estiver fora): ele escuta apenas em `127.0.0.1:19999` na EC2 e não é acessível pela internet. Acesse por túnel SSH: `ssh -i <chave.pem> -L 19999:127.0.0.1:19999 ubuntu@<host da EC2>` e abra `http://localhost:19999`.

## Quem recebe os alertas

| Alerta | Quem recebe |
|---|---|
| UptimeRobot | Ana Flávia (o plano grátis tem um único contato de e-mail) |
| Netdata Cloud | Ana Flávia (e-mail da conta do Cloud, que entra com Google) |

Os e-mails ficam nas contas, não neste arquivo. Se mais alguém precisar receber, o plano grátis do UptimeRobot não permite um segundo contato; uma alternativa é um endereço de grupo que reencaminhe para várias pessoas.

## Alertas ativos e limites

Todos são os alertas padrão do Netdata, sem alteração:

| O quê | Avisa quando |
|---|---|
| Disco (`disk_space_usage`) | Warning acima de 90%; crítico acima de 98% e com menos de 5 GiB livres |
| Memória (`ram_in_use`) | Warning acima de 90%; crítico acima de 98% (cache não conta) |
| CPU (`10min_cpu_usage`) | Média de 10 minutos alta |
| Contêiner (`docker_container_unhealthy`) | Contêiner com healthcheck fica `unhealthy` (api, identifier, rabbitmq e worker têm healthcheck) |
| Contêiner parado (`docker_container_not_running`, alerta próprio) | Warning quando um contêiner passa mais da metade dos últimos 3 minutos parado (`exited`) ou reiniciando (`restarting`); vale para todos os contêineres |
| Outros | `oom_kill`, uso de swap, `load_average_*`, previsão de disco/inodes acabando |
| Servidor | Nó some do Cloud (Unreachable) |

Para referência: os contêineres têm teto de memória no `docker-compose.yml` (api 512m, worker 512m, rabbitmq 1g, identifier 4g).

## O que fazer quando um alerta chega

**"API fora do ar" (UptimeRobot)**

1. Confirme de fora: `curl -I https://api.guaravivo.com.br/health` deve dar `200`.
2. Se não responder, entre na EC2 e rode `docker ps`: todos os contêineres devem estar `Up` (`api`, `identifier` e `rabbitmq` com `(healthy)`).
3. Veja os logs do que estiver caído ou `unhealthy` (`docker logs <contêiner>`) e se houve um deploy recente (aba Actions do `guara-vivo-api`).
4. Se a EC2 responde mas o domínio não, olhe o Caddy, o DNS e o certificado.

**Disco ou memória altos (Netdata)**

- Disco: `df -h /` e `docker system df` mostram o que ocupa espaço (imagens, volumes, logs).
- Memória: `docker stats --no-stream` mostra quem consome. Para saber se um contêiner foi morto por falta de memória: `docker inspect -f '{{.State.OOMKilled}}' <contêiner>`. Se foi, o teto dele no `docker-compose.yml` talvez precise subir.

**Contêiner `unhealthy`**

- `docker ps` e `docker logs <contêiner>`; o healthcheck de cada um está no `Dockerfile` ou no `docker-compose.yml`.
- Worker `unhealthy`: o laço do RabbitMQ parou de responder ou a conexão caiu (o arquivo `/tmp/worker-alive` ficou com mais de 90 s). Veja `docker logs guara-vivo-api-worker-1` e o estado do RabbitMQ.

**Contêiner parado ou reiniciando (Netdata)**

- `docker ps -a` mostra quem está `Exited` ou `Restarting`; `docker logs <contêiner>` mostra por quê. Um contêiner que o `restart: unless-stopped` não consegue manter de pé aparece como `Restarting`.
- Uma parada curta (como a de um deploy, de alguns segundos) não dispara o alerta, de propósito.

**Servidor "Unreachable" no Netdata**

- A EC2 pode estar desligada ou sem rede, ou o Netdata parou. Veja o estado da instância no console da AWS e tente o SSH.

## O que ainda NÃO está coberto

- **Job do worker pendurado:** o healthcheck do worker mede se o laço do RabbitMQ responde, não se o job está andando. Um job travado (por exemplo, uma chamada ao banco sem timeout) mantém o worker `healthy` até o `consumer_timeout` do broker (~30 min). Foi uma escolha para não alarmar por inferência lenta ([documents#71](https://github.com/Guara-vivo/documents/issues/71)).
- **Erros da aplicação:** picos de 401/403/429/5xx e falhas de conexão com o Supabase não aparecem no Netdata. Dependem de captura de erros (Sentry, opcional na #61) ou de leitura dos logs.
- **Vencimento do certificado:** o aviso antecipado é só em plano pago do UptimeRobot. O Caddy renova sozinho; se um dia o certificado vencer, o monitor HTTP falha e o alerta chega, só que sem antecedência.
- **Testes reais de queda:** derrubar a API de propósito (UptimeRobot, critério de aceite da #61) e parar o worker de propósito (Netdata, critério da #71) ainda não foram feitos em horário combinado. O alerta de contêiner parado foi testado com contêineres descartáveis em 2026-11-12 (chegou o e-mail do contêiner parado e do que reinicia em ciclo; a parada de 20 s não alertou).

## Detalhes e cuidados

**UptimeRobot**

- O plano grátis checa com `HEAD` e não deixa trocar para `GET`; por isso `/health` aceita os dois métodos (`guara-vivo-api#24`). Não volte a deixar só `GET`.
- Segundo as páginas oficiais, o plano grátis tem 1 contato de e-mail por conta. Um segundo contato exige plano pago.
- A conta tem 2FA ligada.

**Netdata**

- Roda como contêiner `netdata` (`docker run`, fora do Compose), em rede `host`, com o config em volume. Só escuta em `127.0.0.1` (`[web] bind to = 127.0.0.1` no `netdata.conf` do volume `netdataconfig`).
- Está ligado ao Cloud por um `claim.conf` no volume, com permissão restrita. **O token de claim é segredo: nunca cole em chat, issue ou repositório.** A conexão é só de saída (HTTPS), sem porta de entrada.
- O Cloud recebe metadados (nome do nó, nomes das métricas, estado dos alertas). As métricas em si ficam no servidor.
- O Netdata Cloud não tem 2FA próprio para conta com e-mail e senha; a conta entra com Google e a 2FA é a do Google.
- Para atualizar: baixar a imagem (`docker pull netdata/netdata:stable`), parar e renomear o contêiner antigo, subir o novo com as mesmas opções e conferir saúde, porta e Cloud online. As opções atuais:

```bash
docker run -d --name=netdata \
  --pid=host --network=host \
  -v netdataconfig:/etc/netdata \
  -v netdatalib:/var/lib/netdata \
  -v netdatacache:/var/cache/netdata \
  -v /:/host/root:ro,rslave \
  -v /etc/passwd:/host/etc/passwd:ro \
  -v /etc/group:/host/etc/group:ro \
  -v /etc/localtime:/etc/localtime:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --restart unless-stopped \
  --cap-add SYS_PTRACE \
  --security-opt apparmor=unconfined \
  --security-opt label=disable \
  netdata/netdata:stable
```

**Alerta de contêiner parado (arquivo próprio no Netdata)**

Ele vive só no volume `netdataconfig` da EC2 (`/etc/netdata/health.d/container-parado.conf`), fora de qualquer repositório; por isso o conteúdo fica aqui, para poder ser recriado. Depois de criar ou mudar o arquivo: `docker exec netdata netdatacli reload-health`. A janela de 3 minutos precisa encher, então o alerta fica `UNINITIALIZED` nos primeiros minutos de um contêiner novo.

```
template: docker_container_not_running
      on: docker.container_state
  lookup: average -3m unaligned of exited,restarting
   every: 30s
    warn: $this > 0.5
   delay: down 5m
 summary: Docker container ${label:container_name} parado ou reiniciando
    info: Fracao dos ultimos 3 minutos em que o contêiner esteve parado (exited) ou reiniciando (restarting)
      to: sysadmin
```

Se alguém parar um contêiner de propósito por mais de uns 2 minutos, o alerta vai avisar (é o esperado).

**Healthcheck do worker**

- `liveness.py` (no repositório `guara-vivo-worker`): uma thread pede ao laço do RabbitMQ, a cada 15 s, que grave a hora em `/tmp/worker-alive`; o `HEALTHCHECK` do Docker falha se o arquivo tiver mais de 90 s (intervalo 30 s, timeout 5 s, `start-period` 60 s, 3 tentativas). O worker em inferência longa não fica `unhealthy`, porque o job roda em outra thread.

## Como testar o caminho do alerta

- **UptimeRobot:** crie um monitor temporário apontando para uma rota que devolve 404 (por exemplo `/rota-que-nao-existe`), espere o e-mail e apague o monitor.
- **Netdata:** coloque um alerta temporário e inofensivo em `/etc/netdata/health.d/` (volume `netdataconfig`), rode `docker exec netdata netdatacli reload-health`, confirme o e-mail e **apague o arquivo** e recarregue de novo.
