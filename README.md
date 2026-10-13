# Buscador de vagas

Script em Python que procura vagas de desenvolvimento júnior e estágio na Gupy, no GitHub e nos alertas de e-mail do LinkedIn e do Indeed, junta tudo sem repetir e manda as vagas novas numa mensagem só no Discord. Roda por cron numa VPS Linux, sem IA e sem dependências externas.

```
Gupy + GitHub + alertas do LinkedIn e Indeed  →  filtro (nível, local, área, stack)  →  SQLite (já enviadas?)  →  webhook do Discord
```

## O que ele faz

- **Fontes** (liga e desliga em `FONTES`, no começo do script):
  - **Gupy:** a página de busca do portal, já filtrada por vagas remotas, com até 3 páginas por termo (8 termos, como "desenvolvedor back-end", "desenvolvedor full stack" e "estágio desenvolvimento"), mais uma busca pelas vagas de Franca.
  - **GitHub:** as issues abertas de [backend-br/vagas](https://github.com/backend-br/vagas), pela API pública.
  - **Remotar** e **Programathor:** o código continua aqui, mas **desligado**. Testando os links, o Remotar trazia muito anúncio sem conferência de quem publica (vagas repetidas, anúncios em massa, empresas sem página) e o Programathor quase não tinha vaga júnior. Preferi menos vagas e mais confiáveis.
  - **Alertas de e-mail (LinkedIn e Indeed):** os dois proíbem robô no site, então o script lê os alertas que eles mandam por e-mail, por IMAP, só os dos últimos 3 dias. Só leitura: a caixa é aberta em modo somente leitura e as mensagens são lidas com `BODY.PEEK`, então nada é marcado como lido, apagado ou respondido. É opcional: sem os arquivos `gmail_usuario` e `gmail_senha_app`, essa parte é pulada. O filtro de nível do alerta tem que ser feito no próprio LinkedIn (Estágio, Assistente e Júnior, mais Remoto ou Presencial): meus primeiros alertas não tinham esse filtro e mandavam quase só vaga sênior, que o script descartava. `buscar_vagas.py --email` mostra o que veio nos alertas antes dos filtros e ajuda a descobrir isso.
- **Sem duplicar entre sites:** quando uma vaga da Gupy aparece em outro site, o script lê o número dela dentro do link e reconhece que é a mesma. Vagas com mesma empresa e mesmo título também contam como uma só.
- **Filtro:** só vagas remotas ou em Franca-SP, só de desenvolvimento back-end ou full stack, e sem sênior, especialista ou lead. Pleno só entra quando a vaga é da sua stack (Node/TypeScript), onde a chance é real. Vagas só de front-end (inclusive Angular e Vue), mobile, QA, design, suporte ou low-code ficam de fora. Vaga sem nível no título entra, para decidir na hora.
- **Stack:** o script lê a descrição, os requisitos ou as tecnologias de cada vaga, conforme o que o site mostra. Vaga de outra linguagem (Java, .NET, PHP, Go…) sem Node ou TypeScript fica de fora, exceto estágio e trainee, em que a empresa costuma ensinar. Cada vaga chega marcada como "sua stack", "stack mista" ou "stack não informada".
- **Prioridade:** primeiro as da sua stack; dentro de cada grupo, as com júnior, estágio, trainee ou "nível I" no título.
- **Mensagem:** uma só, com um cartão por grupo (sua stack, stack mista, stack não informada e estágio de outra stack) e o título de cada vaga como link.
- **Arquivo de entrega:** as vagas enviadas nos últimos 3 dias, com a descrição, ficam em `/srv/vagas-entrega/novas.json`. Na VPS, um assistente de IA lê esse arquivo (só leitura) e escolhe as que mais combinam com o currículo. Se a pasta não existir, essa parte é pulada.
- **Sem repetição:** as vagas enviadas ficam num banco SQLite (`vagas.db`). Cada execução manda só as novas, no máximo 25. O que passar disso fica para a próxima.

## Decisões

- **Por que sem IA:** é uma tarefa fixa, sem julgamento. Script é mais barato, mais rápido e dá sempre o mesmo resultado.
- **Por que só biblioteca padrão:** roda em qualquer Python 3 sem `pip install`, direto no Python que já vem no Ubuntu.
- **Sem login e com calma:** o script só lê páginas públicas que os sites permitem no `robots.txt`, espera 2 segundos entre as buscas e roda só 3 vezes por dia. Se um site mudar, ele avisa no log e continua com os outros.
- **Usuário Linux separado:** na VPS, o script roda num usuário só dele, sem sudo. A senha de app do Gmail fica na pasta desse usuário, com permissão 600, longe do assistente de IA que roda na mesma máquina.
- **Segurança:**
  - A URL do webhook fica fora do código, num arquivo com permissão 600 que o `.gitignore` não deixa subir.
  - Títulos de vaga vêm de fora, então o script tira `@` e marcação do Discord e manda `allowed_mentions` vazio. Assim nenhum título consegue mencionar `@everyone` no servidor.

## Como usar

Precisa de Python 3.8 ou mais novo e de um webhook do Discord (no canal: Editar canal → Integrações → Webhooks).

```bash
git clone https://github.com/Joaoxzx/buscador-de-vagas.git ~/buscador-de-vagas
mkdir -p ~/vagas && chmod 700 ~/vagas   # banco, webhook e log ficam aqui, fora do repositório

# guarda a URL do webhook sem mostrar na tela
read -rsp "URL do webhook: " URL; echo "$URL" > ~/vagas/webhook_vagas; chmod 600 ~/vagas/webhook_vagas; unset URL; echo

# teste: só mostra no terminal, não manda nem marca como vista
python3 ~/buscador-de-vagas/buscar_vagas.py --teste

# de verdade
python3 ~/buscador-de-vagas/buscar_vagas.py

# atualizar depois de uma mudança no GitHub
git -C ~/buscador-de-vagas pull
```

Agendamento no cron (horário UTC; 11, 15 e 20 UTC são 8h, 12h e 17h em Brasília):

```
0 11,15,20 * * 1-5 python3 $HOME/buscador-de-vagas/buscar_vagas.py >> $HOME/vagas/log.txt 2>&1
0 15 * * 0,6 python3 $HOME/buscador-de-vagas/buscar_vagas.py >> $HOME/vagas/log.txt 2>&1
```

## Como adaptar

No começo do `buscar_vagas.py`:

- `TERMOS_GUPY` e `TERMOS_REMOTAR`: o que buscar em cada site.
- `PAGINAS_PROGRAMATHOR`: as páginas do Programathor que ele lê.
- `DIAS_MAXIMOS`: até quantos dias de publicada a vaga ainda entra (Remotar).
- `REPOS_GITHUB`: repositórios de vagas.
- `NIVEL_ALTO` e `FORA_DA_AREA`: palavras que tiram a vaga do filtro.
- `MINHA_STACK` e `OUTRA_STACK`: as linguagens que você usa e as que não usa.
- A cidade aceita fica em `buscar_gupy` e `buscar_github` (hoje, Franca).
