# Monitor de Precos de Concorrentes - IDE Livraria

Ferramenta de linha de comando (CLI) que coleta os precos de uma lista de livros monitorados
nos sites de concorrentes, compara com o preco da IDE Livraria e gera um relatorio Excel.

E uma ferramenta **local**, pensada para ser operada por voce (Joao): o cliente nao acessa o
sistema, apenas recebe o relatorio pronto (arquivo `.xlsx` em `reports/`).

> **Quer instalar numa outra maquina (sem precisar configurar nada manualmente)?** Veja o
> [INSTALACAO.md](INSTALACAO.md) - tem um instalador (`instalar.bat`) que cuida de tudo
> (inclusive instalar o Python, se a maquina nao tiver) e abre automaticamente uma **interface
> grafica** (`Monitor de Precos.bat`) para o uso do dia a dia, sem precisar digitar nada no
> terminal.

## Interface grafica

Depois de instalado (ver [INSTALACAO.md](INSTALACAO.md)), o programa abre como uma janela
normal, com:

- **Aba "Livros monitorados"** (fluxo principal): voce cadastra ISBN, titulo, autor e seu
  preco. Pode ser da IDE ou de outra editora. Botoes Novo / Editar / Limpar lista. O atalho
  do catalogo IDE e opcional.
- **Aba "Resultados"**: mostra o resultado da ultima coleta de cada livro em cada concorrente
  (preco efetivo, com a promocao quando houver; diferenca para o preco da IDE; situacao: ok,
  incerto, nao encontrado, erro, sem coleta; disponibilidade; data/hora). Linhas coloridas
  (vermelho = IDE mais cara, verde = mais barata, amarelo = igual), filtros por livro,
  concorrente e situacao, e duplo clique abre a pagina do produto no navegador. Atualiza
  sozinha ao fim de cada coleta, antes de voce gerar o Excel (que usa os mesmos precos).
  Na coleta, o livro e buscado **primeiro pelo ISBN** (digitos, com hifen e ISBN-10, quando
  der) e so se nao aparecer, pelo titulo. Isso evita pegar outra edicao/editora com o mesmo
  nome. Se varios resultados tiverem o mesmo titulo e nao houver ISBN para confirmar, o
  programa marca "nao encontrado" / "incerto" em vez de chutar.
  As listas (livros, catalogo e resultados) tem **paginacao** (5, 10, 20, 50 ou 100 itens por
  pagina). "Sem coleta" significa que o livro ainda nao foi coletado; "-" no preco significa que
  nao ha preco (nao encontrado, erro ou sem coleta). Se um site **bloquear** o robo (firewall,
  captcha), a linha aparece como "erro na coleta" com o motivo, o programa para de insistir
  naquele site na mesma coleta (insistir piora o bloqueio) e nao gasta tokens de IA tentando
  "consertar" o que nao e problema de receita; costuma voltar a funcionar depois de um tempo.
- **Aba "Concorrentes"**: lista os concorrentes com uma coluna **Status da busca** (ok,
  mapeado por IA em tal data, precisa mapear, fora do ar). Botoes: **Mapear com IA**, **Mapear
  todos pendentes**, **Desfazer ultimo mapeamento**, **Novo concorrente...** (so nome e URL) e
  "Editar seletores/URL..." (ajuste manual da receita, sem editar arquivos na mao).
- **Aba "Configuracoes de IA"**: onde voce cola a chave da API do Claude, testa a chave,
  escolhe o modelo e liga/desliga o reparo automatico (ver a secao 4.1 abaixo).
- **Botoes de acao** (sempre visiveis): Coletar tudo, Coleta avulsa (escolhe 1 livro), Gerar
  relatorio, Abrir pasta de relatorios, Smoke-test, **Verificar atualizacoes** (baixa a Release
  publica do GitHub configurada em `config/update.yaml`, sem apagar sua lista/dados).
- **Log de atividades**: mostra o progresso em tempo real de qualquer operacao em andamento
  (a janela nao trava durante a coleta, que pode levar alguns minutos).

Para abrir manualmente: use o atalho **"Monitor de Precos IDE Livraria"** na Area de Trabalho,
ou de dois cliques em `Monitor de Precos.bat` dentro da pasta do projeto.

Se preferir o menu em modo texto (sem janela grafica), use
`Monitor de Precos (modo texto).bat`.

## Visao geral

```
CSV de vendas (opcional) -> import-sales -> lista de livros monitorados (SQLite)
                                                      |
                                                      v
                                                   collect  -> abre cada site concorrente
                                                      |          num navegador headless,
                                                      v          busca cada livro e salva
                                              price_history         o preco encontrado
                                                      |
                                                      v
                                                   report  -> reports/relatorio_precos_<data>.xlsx
```

## 1. Setup inicial (uma vez)

**Opcao recomendada (instalador automatico):** veja [INSTALACAO.md](INSTALACAO.md). Ele cuida
de tudo (ate instalar o Python, se precisar) com um clique duplo em `instalar.bat`.

**Opcao manual (se voce ja e desenvolvedor e prefere rodar os comandos voce mesmo):**

```powershell
cd D:\bot_ide
python -m venv .venv
.\.venv\Scripts\pip.exe install -e .
.\.venv\Scripts\python.exe -m playwright install chromium
```

Isso cria o ambiente virtual, instala as dependencias (click, playwright, SQLAlchemy, pandas,
openpyxl, rapidfuzz, pyyaml) e baixa o navegador Chromium usado pelo Playwright.

## 2. Comandos do dia a dia (modo texto / terminal)

A interface grafica (secao acima) cobre o uso do dia a dia sem precisar de terminal. Os
comandos abaixo sao a mesma funcionalidade via linha de comando - uteis para quem prefere
terminal, para automacao/scripts, ou como alternativa se a interface grafica tiver algum
problema na sua maquina.

Todos os comandos abaixo assumem que voce ja ativou o ambiente, ou usa o caminho completo
`.\.venv\Scripts\id-monitor.exe <comando>`.

### Adicionar um livro manualmente

```powershell
id-monitor add-book --title "O Pequeno Principe" --isbn 9788574061346 --price 24.90
```

### Ver a lista atual de livros monitorados

```powershell
id-monitor list-books
```

### Importar a lista de mais vendidos a partir de um CSV de vendas

O CSV precisa ter pelo menos as colunas `titulo` e `quantidade_vendida` (nomes flexiveis, veja
`src/id_monitor/sales_import.py` para os sinonimos aceitos: `isbn`, `sku`, `preco` sao opcionais).

```powershell
id-monitor import-sales vendas_outubro.csv --top-n 30
id-monitor import-sales vendas_outubro.csv --top-n 20 --mode least_sold
```

Isso ativa os N titulos selecionados e desativa automaticamente os titulos que eram
"auto-selecionados" em um ciclo anterior mas nao entraram na nova lista (livros adicionados
manualmente com `add-book` nunca sao desativados automaticamente).

### Importar titulos, ISBN e precos do catalogo da IDE Editora

O catalogo da loja da IDE (projeto Firebase `ide-digital`, o mesmo do `ide-reader-shop` e do
painel admin) pode ser lido direto, sem digitar nada:

```powershell
id-monitor import-catalog --dry-run      # so mostra o que seria importado
id-monitor import-catalog                # adiciona os livros que faltam e atualiza os precos
id-monitor import-catalog --only-update  # so atualiza preco/ISBN de quem ja esta na lista
```

Na interface nao precisa digitar comando: a aba **"Catalogo IDE"** baixa o catalogo sozinha toda
vez que o programa abre (e guarda uma copia em `data/catalog_cache.json` para abrir na hora, mesmo
sem internet). Nela voce busca por titulo/autor/ISBN, seleciona um ou varios livros (Ctrl/Shift,
Ctrl+A para todos) e clica em **Adicionar selecionados ao monitoramento** (ou duplo clique). Tambem
ha "Adicionar todos os listados" e "Parar de monitorar selecionados" (o historico e mantido). A
cada abertura, os precos dos livros que voce ja monitora sao atualizados automaticamente; livros
novos so entram quando voce escolhe.

- **Acesso**: so leitura e **sem senha**. A colecao `e-books` do Firestore tem leitura publica
  (`allow read: if true` em `firestore.rules`); o programa nao usa `.env.local` nem a chave de
  service account do painel, e nunca grava nada la.
- **Preco**: usa o preco ja com o desconto da loja (`price - price*discount/100`, a mesma conta
  do app). Com `--list-price` usa o preco de tabela.
- **ISBN**: e gravado so com numeros (`9788573414936`), que e o formato que os sites buscam
  melhor. ISBN invalido/ausente vira busca por titulo.
- Ignora itens inativos no catalogo, duplicados e itens com preco simbolico (< R$ 5,00, ex.:
  amostras de R$ 2,00).
- **Atencao**: o catalogo da loja e de **e-books** (a colecao `books`, de livros fisicos, esta
  vazia). Os concorrentes vendem livro fisico, entao o preco do e-book nao e diretamente
  comparavel; se o cliente tiver o preco do fisico, informe-o por CSV (`preco`) ou `add-book`.

### Rodar a coleta de precos

```powershell
# Coleta todos os livros ativos em todos os concorrentes ativos
id-monitor collect

# Coleta avulsa de 1 ou mais livros especificos (nao precisa esperar o ciclo mensal)
id-monitor collect --book "O Pequeno Principe"

# Restringir a coleta a concorrentes especificos (keys definidas em config/competitors.yaml)
id-monitor collect --competitor livraria_cultura --competitor boa_nova
```

Falhas em um site ou produto especifico sao registradas em `logs/collector.log` e marcadas
como "erro na coleta" / "nao encontrado" no relatorio, sem interromper a coleta dos demais.

### Gerar o relatorio

```powershell
id-monitor report
```

Gera `reports/relatorio_precos_<data>.xlsx`: uma linha por titulo, com o preco da IDE Livraria,
o preco e status de cada concorrente, e cor indicando se a IDE Livraria esta mais cara (vermelho),
mais barata (verde) ou no mesmo preco (amarelo) que aquele concorrente. Celulas cinzas indicam
que faltou dado (sem coleta, nao encontrado ou erro).

### Smoke-test (diagnostico rapido dos scrapers)

```powershell
id-monitor smoke-test
```

Faz uma busca generica (por padrao, o termo "livro") em cada concorrente ativo e informa se
cada scraper retornou resultados. Util para descobrir rapidamente se um site mudou de layout
(0 resultados) antes de rodar a coleta completa de novo.

### Atualizacao automatica (GitHub)

Configure `config/update.yaml` com o repo publico (`owner/repo`). Publique Releases com tag
`vX.Y.Z` e o zip do `scripts/criar_pacote_zip.ps1`. Depois:

```powershell
id-monitor check-update
id-monitor self-update --yes
```

Na interface grafica use o botao **Verificar atualizacoes**. Detalhes em [INSTALACAO.md](INSTALACAO.md)
(secao 5).

## 3. Agendamento mensal

Dois scripts em `scripts/` cuidam do agendamento:

- `scripts/run_monthly_cycle.ps1`: roda `collect` + `report` em sequencia e grava um log em
  `logs/monthly_run_<data>.log`. Pode ser executado manualmente a qualquer momento.
- `scripts/register_task_scheduler.ps1`: registra uma tarefa no Windows Task Scheduler que
  chama `run_monthly_cycle.ps1` automaticamente todo mes (por padrao, dia 1 as 08:00).

Para agendar (rodar uma vez, como administrador se necessario):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task_scheduler.ps1
# ou com dia/horario customizados:
powershell -ExecutionPolicy Bypass -File scripts\register_task_scheduler.ps1 -DayOfMonth 5 -Time "09:30"
```

Para testar a tarefa imediatamente: `Start-ScheduledTask -TaskName "IDMonitorPrecosMensal"`.
Para remover: `Unregister-ScheduledTask -TaskName "IDMonitorPrecosMensal" -Confirm:$false`.

## 4. Adicionando ou ajustando um concorrente

### 4.1 Mapeamento por IA (o jeito recomendado)

Cada site busca de um jeito diferente (Fbits, VTEX, loja propria...). Em vez de voce descobrir
a URL de busca e os seletores CSS na mao, a IA (Claude) faz isso:

1. **Uma vez**: cole a chave da API na aba "Configuracoes de IA" (ou rode `id-monitor
   set-api-key`). A chave fica no Cofre de Credenciais do Windows, nunca em arquivo do projeto;
   a variavel de ambiente `ANTHROPIC_API_KEY` tambem funciona.
2. Cadastre o site (aba "Concorrentes" -> "Novo concorrente...", ou `id-monitor add-site "Nome"
   https://site.com.br`) e clique em **Mapear com IA** (ou `id-monitor map-site <key>`).
3. O programa abre o site, acha o campo de busca, pesquisa um termo de teste e manda o HTML
   *simplificado* da pagina de resultados ao Claude, que devolve a URL de busca e os seletores.
   A receita so e salva se passar numa **busca de teste** (itens com titulo e preco legivel); a
   anterior fica guardada em `config/backups/` ("Desfazer ultimo mapeamento" restaura).
4. **Dai em diante a coleta usa so CSS e nao gasta tokens.**

**Reparo automatico** (aba "Configuracoes de IA", ligado por padrao se houver chave): antes de
coletar, cada site recebe uma busca de teste. Se ela falhar:

- se o **site nao responde** (como a Livraria Cultura em 18/11/2026), ele e marcado "fora do ar"
  e **nenhum token e gasto**; a coleta pula esse site e registra o erro no historico;
- se o site responde mas a busca nao traz resultados (**layout mudou**), a IA re-mapeia e so
  salva se a nova receita passar na validacao;
- se o reparo falhar, o programa so tenta de novo depois de 12 horas (para nao gastar tokens a
  cada coleta). Cada mapeamento faz no maximo 5 chamadas a IA.

**Custo**: cada mapeamento envia ate ~24 mil caracteres de HTML (uns 8 mil tokens) por
tentativa, tipicamente 1 a 3 tentativas por site: centavos de dolar por site. Sem chave
configurada, nada muda: o programa funciona como antes, sem chamar IA.

**Privacidade**: so o HTML da pagina de *resultados de busca* dos sites concorrentes vai para a
API; nenhum dado seu (livros, precos, vendas) e enviado.

**Limites conhecidos**: sites com protecao anti-robo (captcha/Cloudflare) nao podem ser
mapeados (o programa avisa); e se o preco so aparece dentro da pagina de cada produto (nao na
lista de resultados), a validacao reprova a receita - use "Editar seletores/URL..." nesse caso.

### 4.2 Configuracao manual

A maioria dos sites tambem e suportada sem codigo, apenas configurando `config/competitors.yaml`:

```yaml
- key: novo_concorrente
  name: "Nome do Concorrente"
  base_url: "https://www.exemplo.com.br"
  module: "id_monitor.scrapers.novo_concorrente"
  class_name: "NovoConcorrenteScraper"
  active: true
  search_url_template: "https://www.exemplo.com.br/busca?q={query}"
  result_item_selector: "li.product"       # cada item de resultado da busca
  title_selector: "h2.title"               # titulo dentro do item
  price_selector: "span.price"             # preco dentro do item
  link_selector: "a"                       # link do produto dentro do item
```

Depois crie um arquivo minimo em `src/id_monitor/scrapers/novo_concorrente.py`:

```python
from id_monitor.scrapers.generic_css import GenericCssScraper

class NovoConcorrenteScraper(GenericCssScraper):
    pass
```

Se o site tiver um comportamento fora do padrao (busca que exige clique, paginacao via JS,
captcha, etc.), sobrescreva o metodo `search()` nessa mesma classe com Playwright direto, em vez
de depender do `GenericCssScraper`.

### Situacao dos concorrentes iniciais (18/11/2026)

| Concorrente | Situacao |
|---|---|
| Boa Nova (`boanova.net`, Fbits) | ativo, **ja mapeado por IA** (18/11/2026) |
| FEB Editora (`febeditora.com.br`) | ativo, mapeado por IA (18/11/2026); o firewall do site costuma bloquear o robo (ver secao Resultados) |
| Livrarias Curitiba (`livrariascuritiba.com.br`, VTEX) | ativo, **ja mapeado por IA** (18/11/2026) |
| Livraria Cultura | desativado: o servidor de busca (`site.livrariacultura.com.br`) esta fora do ar |
| Livraria Candeiro (`livrariacandeeiro.com.br`) | desativado: site institucional, sem loja online visivel |

As receitas dos tres sites ativos ja vem prontas no pacote (`config/competitors.yaml`), entao a
coleta funciona sem chave de IA. Para reativar um concorrente, marque
"Ativo" em "Editar seletores/URL...".

### Ajuste manual dos seletores (sem IA)

Se preferir nao usar IA, ou se a validacao reprovar um site:

1. Rode `id-monitor smoke-test` e veja quais concorrentes retornam 0 resultados.
2. Para cada um que falhar, abra o site manualmente, faca uma busca por um livro que voce sabe
   que existe, abra o DevTools do navegador (F12 -> aba Elements) e identifique:
   - a URL que a busca gera (ajuste `search_url_template`);
   - o elemento que envolve cada resultado da busca (ajuste `result_item_selector`);
   - onde estao o titulo, o preco e o link dentro desse elemento (ajuste os demais seletores).
3. Salve `competitors.yaml` e rode `id-monitor smoke-test` de novo para confirmar.

Isso nao exige alterar nenhum arquivo `.py`.

## 5. Pontos ainda abertos (ver tambem o plano original)

- O preco da propria IDE Livraria por titulo precisa estar disponivel: via o CSV de vendas (se
  incluir a coluna de preco) ou cadastrado manualmente com `add-book --price`.
- Os 3 concorrentes ativos ja vem mapeados. A chave da API do Claude so e necessaria para reparar
  um site que mudou de layout ou mapear um site novo (secao 4.1).
- Se algum site continuar bloqueando mesmo com as mitigacoes atuais (ver `src/id_monitor/scrapers/base.py`:
  user-agent realista, viewport de desktop, locale pt-BR, delays aleatorios, scroll simulado,
  retry com backoff), a proxima linha de evolucao seria avaliar um proxy residencial ou servico
  de resolucao de captcha - o que tem custo recorrente e deve ser validado com o cliente antes de
  implementar.

## 6. Testes automatizados

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

Os testes usam um "concorrente fake" local (servidor HTTP estatico em `tests/fixtures/`) para
validar o fluxo completo (busca -> matching -> historico -> relatorio) sem depender dos sites
reais, que podem estar fora do ar ou bloqueando a automacao a qualquer momento. Os testes da IA
usam um cliente falso (nenhuma chamada real a API, nenhum custo) e um cofre de credenciais em
memoria (nao tocam na sua chave).

## 7. Estrutura do projeto

```
instalar.bat                   Instalador automatico (duplo clique) - ver INSTALACAO.md
install.ps1                    Logica do instalador (chamada por instalar.bat)
"Monitor de Precos.bat"        Abre a interface grafica (duplo clique)
"Monitor de Precos (modo texto).bat"  Alternativa em menu de texto, sem janela grafica
config/competitors.yaml        Cadastro dos concorrentes + seletores de busca
src/id_monitor/
  cli.py                       Comandos da CLI (modo texto/terminal)
  gui/app.py                   Interface grafica (tkinter) - janela principal
  gui/dialogs.py               Formularios da interface grafica (livro, concorrente, import)
  collector.py                 Orquestra a coleta (produtos x concorrentes)
  config.py                    Carrega/edita competitors.yaml e caminhos do projeto
  sales_import.py              Importa CSV de vendas e seleciona top N
  catalog_import.py            Importa titulos/ISBN/precos do catalogo da IDE (Firestore, leitura)
  smoke_test.py                Diagnostico rapido dos scrapers
  ai/                          Mapeamento e auto-reparo de sites com o Claude
    mapper.py                  Descobre a busca + seletores, valida, salva; gatilho de reparo
    client.py / html_trim.py   Cliente da API / reducao do HTML enviado a IA
    secrets.py / settings.py   Chave no cofre do Windows / modelo e opcoes
  matching/matcher.py           ISBN -> titulo exato -> fuzzy (rapidfuzz)
  scrapers/
    base.py                    Interface comum + utilitarios anti-bot
    generic_css.py             Scraper generico configurado via YAML
    <concorrente>.py           Um arquivo fino por concorrente (extensivel)
  reporting/report.py          Gera o relatorio Excel comparativo
  storage/db.py                Modelos SQLite (products, competitors, price_history)
data/monitor.db                Banco SQLite local (nao versionado)
reports/                       Relatorios gerados (nao versionado)
logs/                          Logs de coleta (nao versionado)
scripts/                       Agendamento (Task Scheduler) + scripts/criar_pacote_zip.ps1
tests/                         Testes automatizados (pytest)
```
