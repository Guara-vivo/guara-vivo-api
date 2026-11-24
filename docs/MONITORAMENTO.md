# Monitoramento e alertas

Como saber se a API e o servidor estão bem, onde ver e o que fazer quando um alerta chega. Foi montado na issue [Guara-vivo/documents#61](https://github.com/Guara-vivo/documents/issues/61). Última revisão: 2026-11-19 (gráficos por contêiner e alerta de fila parada, [documents#73](https://github.com/Guara-vivo/documents/issues/73)).

## Visão geral

Duas camadas, uma de fora e uma de dentro do servidor:

| Camada | Ferramenta | O que vigia | Como avisa |
|---|---|---|---|
| De fora | UptimeRobot (plano grátis) | `https://api.guaravivo.com.br/health` e `https://guaravivo.com.br`, a cada 5 minutos | E-mail |
| De dentro | Netdata Cloud (plano grátis) | CPU, memória, disco, contêineres (estado e CPU/memória de cada um), fila do RabbitMQ, rajadas de respostas 5xx da API (pelo log do Caddy) e se o servidor some do Cloud | E-mail (Critical, Warning e Unreachable) |

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

Exceto os quatro marcados como "alerta próprio", são os alertas padrão do Netdata, sem alteração:

| O quê | Avisa quando |
|---|---|
| Disco (`disk_space_usage`) | Warning acima de 90%; crítico acima de 98% e com menos de 5 GiB livres |
| Memória (`ram_in_use`) | Warning acima de 90%; crítico acima de 98% (cache não conta) |
| CPU (`10min_cpu_usage`) | Média de 10 minutos alta |
| Contêiner (`docker_container_unhealthy`) | Contêiner com healthcheck fica `unhealthy` (api, identifier, rabbitmq e worker têm healthcheck) |
| Contêiner parado (`docker_container_not_running`, alerta próprio) | Warning quando um contêiner passa mais da metade dos últimos 3 minutos parado (`exited`) ou reiniciando (`restarting`); vale para todos os contêineres |
| API com muitos 5xx (`api_5xx_burst`, alerta próprio) | Warning quando o Caddy devolve 10 ou mais respostas 5xx nos últimos 10 minutos (inclui os 502 de quando a API está fora e os 5xx que a própria API devolve) |
| Worker sem consumir (`rabbitmq_sem_consumidor`, alerta próprio) | Warning quando a média de consumidores da fila nos últimos 3 minutos fica abaixo de 0,5, ou seja, o worker está sem conexão com o RabbitMQ (mesmo com o contêiner de pé) |
| Fila parada (`rabbitmq_fila_parada`, alerta próprio) | Warning quando a fila de trabalho (`guara-vermelho-inference`) não esvazia nenhuma vez em 15 minutos (o menor número de mensagens prontas na janela fica acima de 0): worker lento, travado ou sem dar conta |
| CPU e memória de um contêiner (`cgroup_10min_cpu_usage`, `cgroup_ram_in_use`) | Alertas padrão por contêiner: média de 10 minutos de CPU alta, ou memória perto do teto do contêiner |
| Outros | `oom_kill`, uso de swap, `load_average_*`, previsão de disco/inodes acabando |
| Servidor | Nó some do Cloud (Unreachable) |

Para referência: os contêineres têm teto de memória no `docker-compose.yml` (api 512m, worker 512m, rabbitmq 1g, identifier 2g).

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

**API com muitos 5xx (Netdata, `api_5xx_burst`)**

- O alerta conta respostas 5xx no log do Caddy, então diz *que* está falhando, não *por quê*. Veja o que respondeu: `sudo grep -E '"status":5[0-9][0-9]' /var/log/caddy/access.log | tail -20` (campos `request.uri` e `status`).
- Muitos 502 em `/health`, `/users/me` ou `/records/progress/ws` indicam a API fora do ar para o Caddy (deploy, reinício ou queda): siga o roteiro de "API fora do ar".
- 5xx em rotas de registros (`/records`) com a API de pé: `docker logs guara-vivo-api-api-1` mostra o erro (falha de Supabase, RabbitMQ ou código) e o painel do Supabase mostra o lado do banco.
- Uma rajada de deploy pode, de vez em quando, passar do limite. No histórico de 10 dias o limite teria avisado 2 vezes; se for deploy, não há o que fazer.

**Worker sem consumir (Netdata, `rabbitmq_sem_consumidor`)**

- Significa que a fila não tem ninguém lendo: registros novos ficam esperando. `docker ps` mostra se o worker está de pé; `docker logs guara-vivo-api-worker-1` mostra se ele está tentando reconectar (a cada 5 s) e por quê.
- Veja também o gráfico `rabbitmq_queue_messages_ready` (mensagens esperando): se está subindo, os registros estão acumulando. Se o RabbitMQ está `healthy` e o worker reiniciou, ele volta sozinho.
- Um reinício de deploy não dispara o alerta: a média de 3 minutos absorve os segundos de reconexão.

**Fila parada (Netdata, `rabbitmq_fila_parada`)**

- Significa que ficou mensagem esperando na fila de trabalho por 15 minutos seguidos: os registros estão demorando para ser analisados. Normalmente a fila esvazia em segundos (de 13 a 19 de novembro ela nunca passou de 0 numa coleta).
- Veja primeiro se o alerta `rabbitmq_sem_consumidor` também chegou (worker desconectado). Se não, o worker está conectado mas lento ou preso num job: `docker logs guara-vivo-api-worker-1` e o gráfico `rabbitmq_queue_messages_unacked` da fila de trabalho (1 = processando um registro). O CPU e a memória do `identifier` e do `worker` (gráficos por contêiner) mostram se a inferência está pesada.
- Muitos registros enviados de uma vez (por exemplo, um celular sincronizando uma saída de campo inteira) também podem disparar; se a fila estiver diminuindo, é só esperar.

**Servidor "Unreachable" no Netdata**

- A EC2 pode estar desligada ou sem rede, ou o Netdata parou. Veja o estado da instância no console da AWS e tente o SSH.

## O que ainda NÃO está coberto

- **Job do worker pendurado:** o healthcheck do worker mede se o laço do RabbitMQ responde, não se o job está andando. Um job travado (por exemplo, uma chamada ao banco sem timeout) mantém o worker `healthy` até o `consumer_timeout` do broker (~30 min). Foi uma escolha para não alarmar por inferência lenta ([documents#71](https://github.com/Guara-vivo/documents/issues/71)).
- **Erros da aplicação:** só rajadas de 5xx (10 ou mais em 10 minutos) avisam. Um erro isolado, os 4xx (401/403/422/429, cheios de robôs no 404) e a causa do erro não aparecem: para isso, `docker logs` e o painel do Supabase. A captura com Sentry (opcional na #61) foi adiada: avaliada a opção de uma inicialização mínima, sem corpo de requisição nem dados pessoais e com limitador de eventos, ficou para quando o log se mostrar insuficiente.
- **Vencimento do certificado:** o aviso antecipado é só em plano pago do UptimeRobot. O Caddy renova sozinho; se um dia o certificado vencer, o monitor HTTP falha e o alerta chega, só que sem antecedência.
- **Fila parada e alertas por contêiner:** carregados em 2026-11-19, ainda não vistos disparando de verdade.
- **Fila de erros:** registro cuja análise falhou vai para `guara-vermelho-inference-error`, que ninguém consome; não há alerta para isso (o gráfico por fila mostra quantos há).
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
  -v /proc:/host/proc:ro \
  -v /sys:/host/sys:ro \
  --restart unless-stopped \
  --cap-add SYS_PTRACE \
  --security-opt apparmor=unconfined \
  --security-opt label=disable \
  netdata/netdata:stable
```

- **Não tire `-v /proc:/host/proc:ro` e `-v /sys:/host/sys:ro`.** Sem eles o Netdata não enxerga os cgroups dos contêineres (`/host/sys/fs/cgroup`) e não há gráficos de CPU e memória por contêiner; foi o que faltou até 2026-11-19 ([documents#73](https://github.com/Guara-vivo/documents/issues/73)). Com eles aparecem os gráficos `cgroup_<contêiner>` (api, worker, identifier, rabbitmq e netdata) e os alertas padrão por contêiner. Não precisou de `--cgroupns=host`.
- `--security-opt label=disable` o Docker já põe sozinho por causa do `--pid=host`; deixar no comando não muda nada.
- Durante a troca, o alerta de contêiner parado avisa do contêiner antigo renomeado e parado (`netdata-old`) se ele ficar parado por mais de uns 2 minutos. Remova o antigo (`docker rm netdata-old`) assim que o novo estiver saudável e online no Cloud.

**Coletores desligados (`/etc/netdata/go.d.conf`)**

O arquivo vive só no volume `netdataconfig`. É uma cópia de `/usr/lib/netdata/conf.d/go.d.conf` com duas linhas embaixo de `modules:` trocadas de `#  <nome>: yes` para `  <nome>: no`:

- `rabbitmq`: o coletor que o Netdata descobre sozinho pelo Docker falhava sempre (porta TLS 15671 recusada; depois, 401 na 15672 por não ter senha) e só fazia ruído no log. As métricas vêm do coletor `prometheus` (abaixo).
- `smbios_memory`: com o `/sys` do host montado, ele passou a ligar o alerta padrão `smbios_memory_comparison_unavailable` (Warning por e-mail). Numa VM da EC2 não há inventário de memória do firmware para comparar, então seria um Warning permanente.

Para recriar:

```bash
docker exec netdata sh -c "cp /usr/lib/netdata/conf.d/go.d.conf /etc/netdata/go.d.conf && sed -i -e 's/^#  rabbitmq: yes/  rabbitmq: no/' -e 's/^#  smbios_memory: yes/  smbios_memory: no/' /etc/netdata/go.d.conf"
docker exec netdata pkill -x go.d.plugin
```

Por ser cópia, ela não acompanha mudanças do arquivo padrão em atualizações do Netdata; se um dia der problema, apague a cópia e refaça o comando.

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

**Respostas 5xx da API (coletor `web_log` e alerta próprio)**

O Caddy grava um log de acesso em JSON (`/var/log/caddy/access.log`, `roll_size 50mb`, `roll_keep 5`, no `Caddyfile`). O Netdata, como root no contêiner, lê esse arquivo pelo mount `/host/root` com o coletor `web_log`. Os dois arquivos abaixo também vivem só no volume `netdataconfig`, fora de qualquer repositório.

`/etc/netdata/go.d/web_log.conf` (no mapeamento, a chave é o campo do log do Caddy e o valor é o nome conhecido do Netdata, sem `$`; só os campos de nível superior são lidos):

```yaml
jobs:
  - name: caddy_api
    path: /host/root/var/log/caddy/access.log
    log_type: json
    json_config:
      mapping:
        status: status
        size: body_bytes_sent
        duration: request_time
```

- O plugin `go.d` só lê esta pasta quando inicia. Depois de criar ou mudar o arquivo, reinicie **só o plugin** (o contêiner e a API continuam de pé; os coletores do `go.d` ficam uns 10 a 30 s sem dados): `docker exec netdata pkill -x go.d.plugin` (`-x` casa o nome exato; sem ele o `pkill` pode matar o próprio `sh`). O Netdata o sobe de novo sozinho. Em `docker logs netdata`, procure `check success ... job=caddy_api`.
- O coletor só conta o que entra no log depois que sobe; um log novo (rotação) é seguido sozinho.
- Os alertas padrão do `web_log` (`web_log_1m_*`) só valem com mais de 120 requisições por minuto, bem acima do tráfego da API, então não disparam. Por isso o alerta próprio abaixo.

`/etc/netdata/health.d/erros-5xx.conf` (depois de criar ou mudar: `docker exec netdata netdatacli reload-health`):

```
template: api_5xx_burst
      on: web_log.status_code_class_responses
  lookup: sum -10m unaligned of 5xx
   every: 30s
    warn: $this >= 10
   delay: down 5m
 summary: API com muitas respostas 5xx
    info: Quantidade de respostas 5xx da API (log do Caddy) nos ultimos 10 minutos
      to: sysadmin
```

- O limite 10 foi escolhido pelo histórico de 3 a 12 de novembro (103 respostas 5xx, em rajadas de 2 a 12 por hora, quase todas de deploy ou reinício): em janelas de 10 minutos, 3 ou mais teria avisado 13 vezes, 5 ou mais 8, 10 ou mais 2 e 15 ou mais nenhuma (o pico foi 12). Reveja se o padrão de deploys ou o volume mudar.
- A unidade do gráfico é `responses/s` com coleta a cada 1 s, então a soma da janela é a contagem de respostas (conferido: 2 chamadas a `/health` e 3 a uma rota inexistente viraram 2 respostas 2xx e 3 respostas 4xx).
- Ainda não foi visto disparando de verdade (não há como gerar 5xx sem derrubar a API); o caminho do e-mail é o mesmo `to: sysadmin` do alerta de contêiner parado, já testado.

**Healthcheck do worker**

- `liveness.py` (no repositório `guara-vivo-worker`): uma thread pede ao laço do RabbitMQ, a cada 15 s, que grave a hora em `/tmp/worker-alive`; o `HEALTHCHECK` do Docker falha se o arquivo tiver mais de 90 s (intervalo 30 s, timeout 5 s, `start-period` 60 s, 3 tentativas). O worker em inferência longa não fica `unhealthy`, porque o job roda em outra thread.

**Fila e consumidores do RabbitMQ (coletor `prometheus` e alerta próprio)**

O RabbitMQ publica as métricas do plugin `rabbitmq_prometheus` na porta 15692, e o `docker-compose.yml` a expõe só em `127.0.0.1:15692` (Guara-vivo/guara-vivo-api#28), como a 5672 e a 15672. O Netdata, na rede do host, lê esse endereço, que não muda quando o contêiner é recriado. O endpoint não tem senha e só traz contagens (sem mensagens nem credenciais).

`/etc/netdata/go.d/prometheus.conf` (o job `rabbitmq_broker` guarda só 5 séries, somadas para todas as filas; o endpoint inteiro tem ~800. O job `rabbitmq_filas` lê `/metrics/per-object`, que separa por fila, e guarda só 2 métricas):

```yaml
jobs:
  - name: rabbitmq_broker
    url: http://127.0.0.1:15692/metrics
    autodetection_retry: 60
    selector:
      allow:
        - rabbitmq_queue_messages_ready
        - rabbitmq_queue_messages_unacked
        - rabbitmq_consumers
        - rabbitmq_connections
        - rabbitmq_queues
  - name: rabbitmq_filas
    url: http://127.0.0.1:15692/metrics/per-object
    autodetection_retry: 60
    selector:
      allow:
        - rabbitmq_queue_messages_ready
        - rabbitmq_queue_messages_unacked
```

- Em Applications, procure `rabbitmq_broker`: `rabbitmq_queue_messages_ready` (esperando), `rabbitmq_queue_messages_unacked` (sendo processados), `rabbitmq_consumers` (1 = worker conectado), `rabbitmq_connections` e `rabbitmq_queues` (2: trabalho e erros). Hoje: 0, 0, 1, 1, 2.
- O `go.d` só lê o arquivo ao iniciar: `docker exec netdata pkill -x go.d.plugin`.
- **`autodetection_retry: 60` é obrigatório.** Sem ele, o `go.d` testa o endereço uma única vez ao iniciar e, se falhar, desiste do job até o próximo reinício do plugin. Isso aconteceu no reboot da EC2 de 2026-11-17: o Docker subiu o Netdata e o RabbitMQ no mesmo segundo, o teste das 17:01:20 UTC deu `check failed: ... EOF` (o RabbitMQ só abriu a porta 15692 às 17:01:26) e o coletor ficou parado por quase 5 horas, sem gráficos e sem o alerta `rabbitmq_sem_consumidor`, até ser reiniciado à mão. Com o retry, o job tenta de novo a cada 60 s até o primeiro teste passar. Vale para qualquer job novo que leia algo que sobe depois do Netdata.
- Depois de um reboot da EC2 ou de recriar o RabbitMQ, confirme que os dados voltaram: em `docker logs netdata`, procure `check success ... job=rabbitmq_broker`, e no painel veja `rabbitmq_consumers` = 1.
- `rabbitmq_filas` gera um gráfico por fila (label `queue`): `guara-vermelho-inference` (trabalho) e `guara-vermelho-inference-error` (registros cuja análise falhou; o worker publica nela e ninguém consome, então o número só sobe). Em `docker logs netdata`, procure `check success ... job=rabbitmq_filas`.
- O coletor `rabbitmq` que o Netdata descobre sozinho está desligado (veja "Coletores desligados").

`/etc/netdata/health.d/rabbitmq-consumidor.conf` (depois de criar ou mudar: `docker exec netdata netdatacli reload-health`):

```
template: rabbitmq_sem_consumidor
      on: prometheus.rabbitmq_broker.rabbitmq_consumers
  lookup: average -3m unaligned
   every: 30s
    warn: $this < 0.5
   delay: down 5m
 summary: RabbitMQ sem consumidor (worker desconectado)
    info: Media de consumidores da fila nos ultimos 3 minutos; abaixo de 0.5 o worker nao esta consumindo
      to: sysadmin
```

- Carregado e `CLEAR` (consumidores = 1) em 2026-11-13; ainda não visto disparando de verdade (exigiria parar o worker, o que entra no teste real da #71: o alerta de consumidor e o de contêiner parado devem chegar).

`/etc/netdata/health.d/fila-acumulada.conf` (depois de criar ou mudar: `docker exec netdata netdatacli reload-health`):

```
template: rabbitmq_fila_parada
      on: prometheus.rabbitmq_filas.rabbitmq_queue_messages_ready
chart labels: queue=guara-vermelho-inference
  lookup: min -15m unaligned
   every: 1m
    warn: $this > 0
   delay: down 5m
 summary: Fila do RabbitMQ sem esvaziar
    info: Menor numero de mensagens prontas na fila de trabalho nos ultimos 15 minutos; acima de 0 significa que a fila nao esvaziou nenhuma vez (worker lento ou travado)
      to: sysadmin
```

- Por que "não esvaziou" e não "passou de N mensagens": de 13 a 19 de novembro, o máximo de mensagens prontas numa coleta foi 0 e o de `unacked` foi 1 (o worker pega um registro por vez e a fila esvazia antes da coleta seguinte, de 10 s). Sem histórico de picos, um limite fixo seria chute. O mínimo da janela só fica acima de 0 se houve mensagem esperando em todas as coletas dos 15 minutos.
- **Só a fila de trabalho** (`chart labels`). Não use o gráfico somado do job `rabbitmq_broker`: ele inclui a fila de erros, que ninguém consome, e a primeira análise que falhasse deixaria o alerta ligado para sempre.
- Com o worker parado, este alerta chega cerca de 15 minutos depois do `rabbitmq_sem_consumidor`, e só se chegar registro nesse meio tempo.
- Fica `UNINITIALIZED` nos primeiros 15 minutos depois de o coletor subir. Carregado em 2026-11-19; ainda não visto disparando de verdade.

## Como testar o caminho do alerta

- **UptimeRobot:** crie um monitor temporário apontando para uma rota que devolve 404 (por exemplo `/rota-que-nao-existe`), espere o e-mail e apague o monitor.
- **Netdata:** coloque um alerta temporário e inofensivo em `/etc/netdata/health.d/` (volume `netdataconfig`), rode `docker exec netdata netdatacli reload-health`, confirme o e-mail e **apague o arquivo** e recarregue de novo.
