# Buscador de vagas

Script em Python que procura vagas de desenvolvimento júnior e estágio na Gupy e nos repositórios de vagas da comunidade no GitHub, e manda as vagas novas num canal do Discord. Roda por cron numa VPS Linux, sem IA e sem dependências externas.

```
Gupy + GitHub  →  filtro (nível, local, área)  →  SQLite (já enviadas?)  →  webhook do Discord
```

## O que ele faz

- **Fontes:** a página de busca do portal da Gupy (8 termos, como "desenvolvedor back-end", "desenvolvedor full stack" e "estágio desenvolvimento") e as issues abertas de [backend-br/vagas](https://github.com/backend-br/vagas), pela API pública do GitHub.
- **Filtro:** só vagas remotas ou em Franca-SP, só de desenvolvimento back-end ou full stack, e sem pleno, sênior, especialista ou lead. Vagas só de front-end, mobile, QA, design ou suporte ficam de fora. Vaga sem nível no título entra, para decidir na hora.
- **Stack:** o script lê a descrição da vaga (Gupy) ou o texto e as etiquetas (GitHub). Vaga de outra linguagem (Java, .NET, PHP, Go…) sem Node ou TypeScript fica de fora, exceto estágio e trainee, em que a empresa costuma ensinar. Cada vaga chega marcada como "sua stack", "stack mista" ou "stack não informada".
- **Prioridade:** primeiro as da sua stack; dentro de cada grupo, as com júnior, estágio, trainee ou "nível I" no título.
- **Mensagem:** uma só, com um cartão por grupo (sua stack, stack mista, stack não informada e estágio de outra stack) e o título de cada vaga como link.
- **Sem repetição:** as vagas enviadas ficam num banco SQLite (`vagas.db`). Cada execução manda só as novas, no máximo 15. O que passar disso fica para a próxima.

## Decisões

- **Por que sem IA:** é uma tarefa fixa, sem julgamento. Script é mais barato, mais rápido e dá sempre o mesmo resultado.
- **Por que só biblioteca padrão:** roda em qualquer Python 3 sem `pip install`, direto no Python que já vem no Ubuntu.
- **Gupy:** não existe API pública documentada. O script lê os dados que já vêm na página de busca do portal, com 2 segundos entre as buscas e só 3 execuções por dia. Se a página mudar, ele avisa no log e continua com o GitHub.
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

- `TERMOS_GUPY`: o que buscar na Gupy.
- `REPOS_GITHUB`: repositórios de vagas.
- `NIVEL_ALTO` e `FORA_DA_AREA`: palavras que tiram a vaga do filtro.
- `MINHA_STACK` e `OUTRA_STACK`: as linguagens que você usa e as que não usa.
- A cidade aceita fica em `buscar_gupy` e `buscar_github` (hoje, Franca).
