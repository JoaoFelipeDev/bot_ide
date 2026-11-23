# Como instalar numa maquina local (passo a passo)

Este guia e para instalar o Monitor de Precos numa maquina Windows local - mesmo uma que nunca
teve Python instalado.

## 1. Gerar o pacote para levar para a outra maquina

Na maquina onde o projeto esta (esta pasta), rode:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\criar_pacote_zip.ps1
```

Isso cria um arquivo `MonitorPrecosIDELivraria_<data>.zip` na pasta **acima** desta (ou seja, se
o projeto esta em `D:\bot_ide`, o zip aparece em `D:\`). O zip nao inclui `.venv`, banco de
dados, relatorios ou logs - so o codigo e os arquivos de configuracao, ja que o instalador
recria tudo isso na maquina de destino.

Copie esse `.zip` para a maquina de destino (pendrive, e-mail, OneDrive, etc.).

## 2. Instalar na maquina de destino

> **Erro comum:** abrir o `instalar.bat` **de dentro** do WinRAR/Explorer do `.zip`.
> O caminho vira algo como `...\Temp\Rar$DI...\install.ps1` e a instalacao falha.
> Sempre **extraia** o zip primeiro (botao direito -> "Extrair para...").

1. Clique com o botao direito no `.zip` e escolha **Extrair para...** (ex.: `C:\MonitorPrecos`).
   Nao rode de dentro do zip.
2. Abra a pasta extraida e de dois cliques em **`instalar.bat`** (ha tambem um `LEIA-ME.txt`).
3. Uma janela preta (terminal) vai abrir. Aperte uma tecla para comecar.
4. Aguarde - o instalador vai, na ordem:
   - Verificar se ja existe Python 3.11+ na maquina; se nao existir, baixar e instalar o
     Python automaticamente (sem precisar de permissao de administrador).
   - Criar o ambiente do programa dentro da propria pasta (`.venv`).
   - Instalar as bibliotecas necessarias.
   - Baixar o navegador (Chromium) usado para acessar os sites dos concorrentes (~150 MB,
     pode levar alguns minutos dependendo da internet).
   - Criar um atalho **"Monitor de Precos IDE Livraria"** na Area de Trabalho.
5. Quando aparecer "Instalacao concluida!", **o programa abre automaticamente**. Pode fechar
   a janela preta do instalador.

> A maquina precisa estar conectada a internet durante a instalacao (para baixar o Python, se
> precisar, e o navegador Chromium). Depois de instalado, o uso do dia a dia tambem precisa de
> internet (para acessar os sites dos concorrentes), mas nao precisa mais baixar nada.

## 3. Uso do dia a dia

Depois de instalado, use o atalho **"Monitor de Precos IDE Livraria"** na Area de Trabalho (ou
o arquivo `Monitor de Precos.bat` dentro da pasta). Isso abre a interface grafica do programa,
com cinco abas e uma barra de botoes de acao:

- **Aba "Livros monitorados"** (fluxo principal): cadastre ISBN, titulo, autor e seu preco.
  Pode ser livro da IDE ou de outra editora. Botoes Novo / Editar / Limpar lista.
- **Aba "Atalho catalogo IDE (opcional)"**: so se quiser puxar um titulo do catalogo da IDE.
  Nao e obrigatorio; o cliente alimenta a lista na aba de livros.
- **Aba "Resultados"**: o que a ultima coleta encontrou, livro por livro e concorrente por
  concorrente (preco, diferenca para a IDE, situacao, link), com cores e filtros. Abre sozinha
  quando uma coleta termina, para voce conferir antes de gerar o relatorio Excel.
- **Aba "Concorrentes"**: lista os concorrentes e o status da busca de cada um. Botoes "Mapear
  com IA", "Mapear todos pendentes", "Novo concorrente..." e "Editar seletores/URL..." (ver
  secao 4 abaixo).
- **Aba "Configuracoes de IA"**: onde voce cola a chave da API do Claude (ver secao 4).
- **Botoes de acao** (sempre visiveis, nao importa a aba): Coletar tudo, Coleta avulsa,
  Gerar relatorio, Abrir pasta de relatorios, Smoke-test, Verificar atualizacoes.
- **Log de atividades**: mostra o que esta acontecendo em tempo real durante qualquer operacao.

Prefere um menu de texto em vez de janela grafica? Use
`Monitor de Precos (modo texto).bat` na mesma pasta.

## 4. Antes do primeiro uso "de verdade"

1. **Opcional** (so para a IA reparar sites que mudarem ou mapear sites novos): crie uma chave da API em console.anthropic.com (precisa de um pouco de credito), abra a aba
   **"Configuracoes de IA"**, cole a chave, clique em **"Salvar chave"** e depois em **"Testar
   chave"**. A chave fica guardada no Cofre de Credenciais do Windows.
2. Os 3 concorrentes (Boa Nova, FEB e Livrarias Curitiba) **ja vem mapeados**: a coleta funciona
   sem chave e **nao gasta nada**. Se um dia entrar um site novo ou um deles mudar de layout,
   use "Mapear com IA" (ou deixe o reparo automatico agir). Use "Desfazer ultimo mapeamento"
   se algo ficar estranho.
3. Se algum site falhar no mapeamento, selecione-o e clique em **"Editar seletores/URL..."**
   para ajustar na mao (veja a secao 4.2 do [README.md](README.md)), e use o **"Smoke-test"**
   para conferir.
4. Na aba **"Livros monitorados"**, clique em **"Novo livro..."** e preencha ISBN (importante),
   titulo, autor e seu preco. Se a lista antiga estiver cheia, use **"Limpar lista..."** e
   cadastre so o que importa.
5. Clique em **"Coletar tudo"** e depois em **"Gerar relatorio"**.

Sem chave de IA o programa funciona normalmente com os 3 sites que ja vem prontos; so nao
consegue mapear sites novos nem se consertar sozinho. Se um site mudar de layout no futuro e a chave estiver
configurada, a IA conserta a busca sozinha na proxima coleta.

## 5. Reinstalando ou atualizando

### Atualizacao automatica (GitHub Releases)

O programa pode puxar versoes novas de um **repositorio publico** no GitHub:

1. Crie um repo publico (ex.: `sua-org/monitor-precos-concorrentes`).
2. Em `config/update.yaml`, troque `SEU_USUARIO/monitor-precos-concorrentes` pelo `owner/repo` real
   (ou defina a variavel de ambiente `MONITOR_UPDATE_REPO`).
3. Gere o zip: `powershell -ExecutionPolicy Bypass -File scripts\criar_pacote_zip.ps1`
4. No GitHub: **Releases** -> **Draft a new release** -> tag `v0.2.0` (mesma versao do
   `pyproject.toml`) -> anexe o `.zip` -> Publish.
5. No cliente: botao **Verificar atualizacoes**, ou `id-monitor check-update` /
   `id-monitor self-update`.

A atualizacao preserva `data/`, `.venv`, `logs/`, `reports/`, `config/competitors.yaml` e
`config/update.yaml` (o apontamento do repo do cliente nao e sobrescrito).

### Atualizacao manual (zip)

Se preferir sem GitHub: gere um novo `.zip`, extrair por cima da pasta antiga (ou numa pasta
nova) e rode `instalar.bat` de novo - ele reaproveita o ambiente virtual se ja estiver valido.
Seus dados (`data\monitor.db`) e relatorios (`reports\`) nao sao apagados pelo instalador.

## 6. Problemas comuns

- **"Nao foi possivel conectar"/instalacao trava no download**: confirme que a maquina tem
  acesso a internet e que nenhum firewall/antivirus esta bloqueando `python.org` ou
  `playwright.dev`.
- **Antivirus bloqueia o instalador do Python ou o Chromium**: alguns antivirus corporativos
  bloqueiam downloads automatizados. Pode ser necessario liberar a pasta do projeto ou pedir
  ajuda do time de TI.
- **"Monitor de Precos.bat" diz que o programa nao foi instalado**: rode `instalar.bat` primeiro
  e espere a mensagem "Instalacao concluida!" aparecer.
- **A janela grafica nao abre (nada acontece ao clicar)**: tente `Monitor de Precos (modo
  texto).bat` para confirmar se o programa em si esta funcionando. Se o modo texto funcionar
  mas a janela grafica nao abrir, pode ser que o Python tenha sido instalado sem o componente
  "tcl/tk" (raro, mas pode acontecer em instalacoes customizadas) - rode `instalar.bat` de novo.
