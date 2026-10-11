#!/usr/bin/env python3
"""Busca vagas novas em vários sites e nos alertas de e-mail e manda no #vagas.

Só script, sem IA. As vagas já enviadas ficam em vagas.db para não repetir.
Uso: buscar_vagas.py           -> busca e manda no Discord
     buscar_vagas.py --teste   -> só mostra no terminal, não manda nem marca como vista
     buscar_vagas.py --email   -> só mostra o que conseguiu ler dos alertas de e-mail, antes dos filtros
"""
import base64
import datetime
import email
import email.policy
import hashlib
import html
import imaplib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PASTA = Path(os.environ.get("VAGAS_DIR", Path.home() / "vagas"))
BANCO = PASTA / "vagas.db"
ARQUIVO_WEBHOOK = PASTA / "webhook_vagas"
# Alertas por e-mail (LinkedIn, Indeed): só funciona se esses dois arquivos existirem
ARQUIVO_GMAIL_USUARIO = PASTA / "gmail_usuario"
ARQUIVO_GMAIL_SENHA = PASTA / "gmail_senha_app"
BUSCA_EMAIL = "from:(jobalerts-noreply@linkedin.com OR indeed.com) newer_than:3d"
LINKS_GENERICOS = re.compile(r"^(ver vaga|ver todas|candidat|view job|see all|apply|saiba mais|gerenciar|ver mais)", re.I)
MAX_POR_EXECUCAO = 25  # o que passar disso fica para a próxima execução
DIAS_MAXIMOS = 30  # vagas publicadas há mais tempo que isso ficam de fora

# Fontes ligadas (True) e desligadas (False)
FONTES = {
    "email": True,          # alertas do LinkedIn e do Indeed
    "gupy": True,
    "github": True,
    "remotar": False,       # desligado: junta anúncio de qualquer lugar sem conferir quem publica
    "programathor": False,  # desligado: quase não tem vaga júnior
}

TERMOS_GUPY = [
    "desenvolvedor back-end",
    "desenvolvedor backend",
    "desenvolvedor full stack",
    "desenvolvedor node",
    "desenvolvedor typescript",
    "desenvolvedor júnior",
    "estágio desenvolvimento",
    "estágio programação",
]
REPOS_GITHUB = ["backend-br/vagas"]
PAGINAS_PROGRAMATHOR = [
    "https://programathor.com.br/jobs?expertise=J%C3%BAnior",
    "https://programathor.com.br/jobs?expertise=Est%C3%A1gio",
]
TERMOS_REMOTAR = [
    "desenvolvedor junior",
    "desenvolvedor back-end",
    "desenvolvedor full stack",
    "desenvolvedor node",
    "estágio desenvolvimento",
]

# Título com nível acima de júnior fica de fora
NIVEL_ALTO = re.compile(
    r"\b(pleno|pl|s[êe]nior|sr|especialista|lead|l[íi]der|coordenador|gerente|staff|master|arquitet[oa]|ii|iii)\b",
    re.I,
)
# Título precisa ser de desenvolvimento
DESENVOLVIMENTO = re.compile(
    r"desenvolv|developer|programador|programação|software|back.?end|full.?stack|\bti\b|sistemas",
    re.I,
)
# Vaga só de front-end, mobile, QA, design ou suporte fica de fora (full stack e back-end ficam)
FORA_DA_AREA = re.compile(
    r"front.?end|mobile|android|\bios\b|flutter|react native|angular|\bvue|\bqa\b|design|\bux\b|suporte|\bsap\b"
    r"|salesforce|power platform|low.?code|\brpa\b|wordpress"
    r"|desenvolvimento de neg[óo]cios|comercial|vendas|marketing|contab|recursos humanos",
    re.I,
)
BACK_OU_FULL = re.compile(r"back.?end|full.?stack|node|\bapi", re.I)
NIVEL_JUNIOR = re.compile(r"j[úu]nior|\bjr\b|est[áa]gi|trainee|\bi\b", re.I)
SO_EXTERIOR = re.compile(r"somente (eua|usa|eua/canad[áa])|only us|usa only", re.I)
ESTAGIO = re.compile(r"est[áa]gi|trainee", re.I)

# Stack que você usa, e linguagens que você não usa
MINHA_STACK = re.compile(
    r"\bnode|typescript|javascript|\bjs\b|\bts\b|fastify|\bexpress\b|nest\.?js|prisma|next\.?js|\breact\b",
    re.I,
)
OUTRA_STACK = re.compile(
    r"\bjava\b|kotlin|\bspring\b|\.net\b|c#|\bphp\b|laravel|\bruby\b|\brails\b|golang|\bgo\b|delphi|cobol|abap"
    r"|elixir|\bscala\b|\brust\b|c\+\+",
    re.I,
)

TIPOS_GUPY = {
    "vacancy_type_effective": "CLT",
    "vacancy_legal_entity": "PJ",
    "vacancy_type_internship": "Estágio",
    "vacancy_type_apprentice": "Jovem aprendiz",
    "vacancy_type_trainee": "Trainee",
    "vacancy_type_talent_pool": "Banco de talentos",
    "vacancy_type_temporary": "Temporário",
}


def na_area(titulo):
    if not DESENVOLVIMENTO.search(titulo):
        return False
    return not (FORA_DA_AREA.search(titulo) and not BACK_OU_FULL.search(titulo))


def classificar_stack(titulo, texto):
    """Devolve a etiqueta da stack, ou None quando a vaga é de outra linguagem e deve ficar de fora.

    Estágio e trainee com outra linguagem ficam, porque nesses a empresa costuma ensinar.
    """
    if OUTRA_STACK.search(titulo) and not MINHA_STACK.search(titulo):
        return "outra stack" if ESTAGIO.search(titulo) else None
    minha = MINHA_STACK.search(titulo) or MINHA_STACK.search(texto)
    outra = OUTRA_STACK.search(titulo) or OUTRA_STACK.search(texto)
    if minha and outra:
        return "stack mista"
    if minha:
        return "sua stack ✅"
    if outra:
        return "outra stack" if ESTAGIO.search(titulo) else None
    return "stack não informada"


def baixar(url):
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (busca pessoal de vagas)"})
    with urllib.request.urlopen(pedido, timeout=30) as resposta:
        return resposta.read().decode("utf-8")


def id_gupy(url):
    """Vagas da Gupy aparecem em outros sites com o mesmo número dentro do link. Assim a mesma vaga não chega duas vezes."""
    achado = re.search(r"gupy\.io/job/([A-Za-z0-9_=-]+)", url or "")
    if not achado:
        return None
    try:
        codigo = achado.group(1)
        dados = json.loads(base64.urlsafe_b64decode(codigo + "=" * (-len(codigo) % 4)))
        return f"gupy:{dados['jobId']}"
    except Exception:
        return None


def buscar_gupy():
    vagas = []
    for termo in TERMOS_GUPY:
        url = "https://portal.gupy.io/job-search/term=" + urllib.parse.quote(termo)
        try:
            pagina = baixar(url)
            dados = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pagina, re.S)
            lista = json.loads(dados.group(1))["props"]["pageProps"]["initialJobList"]["data"]
        except Exception as erro:
            print(f"Gupy falhou no termo '{termo}': {erro}", file=sys.stderr)
            continue
        for v in lista:
            titulo = v.get("name", "").strip()
            remoto = v.get("workplaceType") == "remote"
            em_franca = (v.get("city") or "").strip().lower() == "franca"
            if not (remoto or em_franca):
                continue
            if NIVEL_ALTO.search(titulo) or not na_area(titulo):
                continue
            stack = classificar_stack(titulo, v.get("description") or "")
            if stack is None:
                continue
            if remoto:
                local = "Remoto"
            else:
                local = {"hybrid": "Híbrido", "on-site": "Presencial"}.get(v.get("workplaceType"), "Presencial") + " em Franca"
            vagas.append({
                "id": f"gupy:{v['id']}",
                "titulo": titulo,
                "empresa": v.get("careerPageName", "").strip(),
                "local": local,
                "tipo": TIPOS_GUPY.get(v.get("type"), "não informado"),
                "url": v.get("jobUrl", ""),
                "fonte": "Gupy",
                "stack": stack,
            })
        time.sleep(2)  # sem pressa, para não sobrecarregar o site
    return vagas


def buscar_github():
    vagas = []
    for repo in REPOS_GITHUB:
        try:
            itens = json.loads(baixar(f"https://api.github.com/repos/{repo}/issues?state=open&per_page=30"))
        except Exception as erro:
            print(f"GitHub falhou em {repo}: {erro}", file=sys.stderr)
            continue
        for item in itens:
            if "pull_request" in item:
                continue
            titulo = item["title"].strip()
            etiquetas = {e["name"].lower() for e in item.get("labels", [])}
            junior = bool(etiquetas & {"júnior", "junior", "estágio", "estagio", "trainee"}) or bool(NIVEL_JUNIOR.search(titulo))
            nivel_alto = bool(etiquetas & {"pleno", "sênior", "senior", "especialista"}) or bool(NIVEL_ALTO.search(titulo))
            if (nivel_alto and not junior) or not na_area(titulo):
                continue
            remoto = "remoto" in etiquetas or re.search(r"remot", titulo, re.I)
            em_franca = re.search(r"\bfranca\b", titulo, re.I)
            if not (remoto or em_franca) or SO_EXTERIOR.search(titulo):
                continue
            stack = classificar_stack(titulo, (item.get("body") or "") + " " + " ".join(etiquetas))
            if stack is None:
                continue
            contratos = [c for c in ("CLT", "PJ", "Estágio") if c.lower() in etiquetas]
            vagas.append({
                "id": f"github:{repo}#{item['number']}",
                "titulo": titulo,
                "empresa": repo,
                "local": "Remoto" if remoto else "Franca",
                "tipo": ", ".join(contratos) or "não informado",
                "url": item["html_url"],
                "fonte": "GitHub",
                "stack": stack,
            })
    return vagas


def sem_tags(texto):
    return html.unescape(re.sub(r"<[^>]+>", " ", texto or "")).strip()


def buscar_programathor():
    """Programathor: só vagas de tecnologia. A página de busca já vem filtrada por nível."""
    vagas = []
    for url in PAGINAS_PROGRAMATHOR:
        try:
            pagina = baixar(url)
        except Exception as erro:
            print(f"Programathor falhou em {url}: {erro}", file=sys.stderr)
            continue
        for link, cartao in re.findall(r'<div class="cell-list[^"]*">\s*<a href="(/jobs/\d+[^"]*)">(.*?)</a>', pagina, re.S):
            if "vencida" in cartao.lower():  # vaga encerrada
                continue
            titulo = sem_tags(re.search(r"<h3[^>]*>(.*?)</h3>", cartao, re.S).group(1)).replace("NOVA", "").strip()
            # cada informação vem num <span> com um ícone que diz o que ela é
            info = {icone: sem_tags(texto) for icone, texto in re.findall(r"<span><i class='([^']+)'></i>(.*?)</span>", cartao, re.S)}
            local = next((v for k, v in info.items() if "map-marker" in k), "")
            empresa = next((v for k, v in info.items() if "briefcase" in k), "")
            contrato = next((v for k, v in info.items() if "file-alt" in k), "não informado")
            tecnologias = re.findall(r"tag-list[^>]*>([^<]+)<", cartao)
            remoto = "remoto" in local.lower()
            if not (remoto or "franca" in local.lower()):
                continue
            if NIVEL_ALTO.search(titulo) or not na_area(titulo):
                continue
            stack = classificar_stack(titulo, " ".join(tecnologias))
            if stack is None:
                continue
            vagas.append({
                "id": "programathor:" + re.search(r"/jobs/(\d+)", link).group(1),
                "titulo": titulo,
                "empresa": empresa,
                "local": "Remoto" if remoto else local,
                "tipo": contrato,
                "url": "https://programathor.com.br" + link,
                "fonte": "Programathor",
                "stack": stack,
            })
        time.sleep(2)
    return vagas


def buscar_remotar():
    """Remotar: vagas remotas no Brasil. Usa o mesmo endereço de dados que o site usa para mostrar a busca."""
    vagas = []
    for termo in TERMOS_REMOTAR:
        url = "https://api.remotar.com.br/jobs?active=true&page=1&search=" + urllib.parse.quote(termo)
        try:
            lista = json.loads(baixar(url))["data"]
        except Exception as erro:
            print(f"Remotar falhou no termo '{termo}': {erro}", file=sys.stderr)
            continue
        for v in lista:
            titulo = (v.get("title") or "").strip()
            if v.get("type") != "remote" and (v.get("city") or "").strip().lower() != "franca":
                continue
            try:
                publicada = datetime.datetime.fromisoformat(v["createdAt"])
                if datetime.datetime.now(publicada.tzinfo) - publicada > datetime.timedelta(days=DIAS_MAXIMOS):
                    continue
            except (KeyError, TypeError, ValueError):
                pass
            if NIVEL_ALTO.search(titulo) or not na_area(titulo):
                continue
            requisitos = " ".join(r.get("description") or "" for r in v.get("jobRequirements") or [])
            texto = " ".join([v.get("subtitle") or "", sem_tags(v.get("description")), requisitos])
            stack = classificar_stack(titulo, texto)
            if stack is None:
                continue
            link = v.get("externalLink") if v.get("isExternalLink") and v.get("externalLink") else f"https://remotar.com.br/job/{v['id']}"
            empresa = (v.get("company") or {}).get("name") or v.get("companyDisplayName") or ""
            vagas.append({
                "id": id_gupy(link) or f"remotar:{v['id']}",
                "titulo": titulo,
                "empresa": empresa,
                "local": "Remoto" if v.get("type") == "remote" else "Franca",
                "tipo": "não informado",
                "url": link,
                "fonte": "Remotar",
                "stack": stack,
            })
        time.sleep(2)
    return vagas


def pasta_todos_os_emails(imap):
    """O nome da pasta 'Todos os e-mails' muda com o idioma do Gmail; ela é a que tem a marca \\All."""
    _, linhas = imap.list()
    for linha in linhas:
        texto = linha.decode("utf-8", "replace")
        if "\\All" in texto:
            return texto.rsplit(' "/" ', 1)[-1]
    return "INBOX"


def vagas_do_html(corpo, fonte):
    """Acha os links de vaga num e-mail de alerta e o texto 'Empresa · Local' que vem logo depois de cada um."""
    padrao_link = r"linkedin\.com/(?:comm/)?jobs/view/(\d+)" if fonte == "LinkedIn" else r"indeed\."
    links = [m for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', corpo, re.S | re.I)
             if re.search(padrao_link, html.unescape(m.group(1)))]
    vagas, vistos = [], set()
    for n, m in enumerate(links):
        url = html.unescape(m.group(1))
        titulo = sem_tags(m.group(2))
        if len(titulo) < 6 or LINKS_GENERICOS.search(titulo):
            continue
        fim = links[n + 1].start() if n + 1 < len(links) else len(corpo)
        linhas = [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", corpo[m.end():fim])).splitlines() if l.strip()]
        detalhe = next((l for l in linhas if "·" in l), linhas[0] if linhas else "")
        empresa, _, local = (p.strip() for p in detalhe.partition("·"))
        if fonte == "LinkedIn":
            numero = re.search(padrao_link, url).group(1)
            id_vaga, url = f"linkedin:{numero}", f"https://www.linkedin.com/jobs/view/{numero}/"
        else:
            id_vaga = "indeed:" + hashlib.sha1(f"{titulo}|{empresa}".lower().encode()).hexdigest()[:16]
        if id_vaga in vistos:
            continue
        vistos.add(id_vaga)
        vagas.append({"id": id_vaga, "titulo": titulo, "empresa": empresa, "local": local, "url": url, "fonte": fonte})
    return vagas


def ler_alertas_email():
    """Lê os alertas de vaga dos últimos 3 dias. Só leitura: não marca como lido, não apaga, não responde."""
    if not (ARQUIVO_GMAIL_USUARIO.exists() and ARQUIVO_GMAIL_SENHA.exists()):
        return [], 0
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(ARQUIVO_GMAIL_USUARIO.read_text().strip(), ARQUIVO_GMAIL_SENHA.read_text().strip())
        imap.select(pasta_todos_os_emails(imap), readonly=True)
        _, numeros = imap.search(None, "X-GM-RAW", f'"{BUSCA_EMAIL}"')
        ids = numeros[0].split()[-30:]  # no máximo os 30 alertas mais recentes
        vagas = []
        for i in ids:
            _, dados = imap.fetch(i, "(BODY.PEEK[])")
            mensagem = email.message_from_bytes(dados[0][1], policy=email.policy.default)
            fonte = "LinkedIn" if "linkedin" in (mensagem.get("From") or "").lower() else "Indeed"
            parte = mensagem.get_body(preferencelist=("html",))
            if parte is not None:
                vagas += vagas_do_html(parte.get_content(), fonte)
        imap.logout()
        return vagas, len(ids)
    except Exception as erro:
        print(f"E-mail falhou: {erro}", file=sys.stderr)
        return [], 0


def buscar_email():
    vagas = []
    for v in ler_alertas_email()[0]:
        remoto = "remot" in v["local"].lower()
        if not (remoto or "franca" in v["local"].lower()):
            continue
        if NIVEL_ALTO.search(v["titulo"]) or not na_area(v["titulo"]):
            continue
        stack = classificar_stack(v["titulo"], "")
        if stack is None:
            continue
        vagas.append({**v, "local": "Remoto" if remoto else v["local"], "tipo": "não informado", "stack": stack})
    return vagas


def abrir_banco():
    banco = sqlite3.connect(BANCO)
    banco.execute(
        "CREATE TABLE IF NOT EXISTS vistas (id TEXT PRIMARY KEY, fonte TEXT, titulo TEXT,"
        " empresa TEXT, url TEXT, enviada_em TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    return banco


def limpar(texto):
    # títulos vêm de fora: tira marcação do Discord para não quebrar a mensagem nem o link
    return re.sub(r"[*_`~|>@\[\]]", "", texto).strip()


# Um cartão (embed) por grupo, nesta ordem, com a cor da barra lateral
GRUPOS = [
    ("sua stack", "🟢 Sua stack", 0x2ECC71),
    ("stack mista", "🟡 Stack mista: abra e veja os requisitos", 0xF1C40F),
    ("stack não informada", "⚪ Stack não informada: abra e veja os requisitos", 0x95A5A6),
    ("outra stack", "🔵 Estágio de outra stack: a empresa costuma ensinar", 0x3498DB),
]
LIMITE_CARTAO = 4000      # o Discord aceita até 4096 caracteres por cartão
LIMITE_MENSAGEM = 5800    # e até 6000 somando os cartões de uma mensagem


def montar_mensagens(vagas):
    """Uma mensagem com um cartão por grupo. Só quebra em mais mensagens se passar do limite do Discord."""
    cartoes = []
    for chave, nome, cor in GRUPOS:
        do_grupo = [v for v in vagas if v["stack"].startswith(chave)]
        partes, atual = [], ""
        for v in do_grupo:
            linha = f"**[{limpar(v['titulo'])}]({v['url']})**\n{limpar(v['empresa'])} · {v['local']} · {v['tipo']} · {v['fonte']}"
            if atual and len(atual) + len(linha) + 2 > LIMITE_CARTAO:
                partes.append(atual)
                atual = linha
            else:
                atual = f"{atual}\n\n{linha}" if atual else linha
        if atual:
            partes.append(atual)
        for parte in partes:
            cartoes.append({"title": f"{nome} ({len(do_grupo)})", "description": parte, "color": cor})

    mensagens, atual, tamanho = [], [], 0
    for cartao in cartoes:
        peso = len(cartao["title"]) + len(cartao["description"])
        if atual and (tamanho + peso > LIMITE_MENSAGEM or len(atual) == 10):
            mensagens.append(atual)
            atual, tamanho = [], 0
        atual.append(cartao)
        tamanho += peso
    if atual:
        mensagens.append(atual)
    return [{"content": f"**{len(vagas)} vaga(s) nova(s)**" if i == 0 else "", "embeds": m}
            for i, m in enumerate(mensagens)]


def enviar(webhook, mensagem):
    corpo = json.dumps({**mensagem, "allowed_mentions": {"parse": []}}).encode("utf-8")
    pedido = urllib.request.Request(
        webhook, data=corpo, headers={"Content-Type": "application/json", "User-Agent": "buscar-vagas"}
    )
    urllib.request.urlopen(pedido, timeout=30).close()
    time.sleep(1)


def main():
    teste = "--teste" in sys.argv
    if "--email" in sys.argv:
        vagas, quantos = ler_alertas_email()
        print(f"{quantos} e-mails de alerta lidos, {len(vagas)} vagas encontradas neles (antes dos filtros):")
        for v in vagas:
            print(f"- {v['titulo']} | {v['empresa']} | {v['local']} | {v['fonte']}")
        return
    PASTA.mkdir(parents=True, exist_ok=True)
    banco = abrir_banco()

    # a Gupy vem por último: quando a mesma vaga aparece em dois lugares, fica a versão com os dados da Gupy
    buscas = [("email", buscar_email), ("remotar", buscar_remotar), ("programathor", buscar_programathor),
              ("github", buscar_github), ("gupy", buscar_gupy)]
    encontradas = {v["id"]: v for nome, buscar in buscas if FONTES[nome] for v in buscar()}
    vistas = {linha[0] for linha in banco.execute("SELECT id FROM vistas")}
    # algumas empresas publicam a mesma vaga várias vezes: mesma empresa + mesmo título conta como uma só
    novas, ja_tem = [], set()
    for i, v in encontradas.items():
        chave = (v["empresa"].lower(), v["titulo"].lower())
        if i not in vistas and chave not in ja_tem:
            novas.append(v)
            ja_tem.add(chave)
    # sua stack primeiro; dentro de cada grupo, júnior e estágio primeiro
    novas.sort(key=lambda v: (not v["stack"].startswith("sua stack"), not NIVEL_JUNIOR.search(v["titulo"])))
    enviar_agora = novas[:MAX_POR_EXECUCAO]
    print(f"{len(encontradas)} vagas no filtro, {len(novas)} novas, {len(enviar_agora)} para enviar agora.")

    if not enviar_agora:
        return
    if teste:
        for mensagem in montar_mensagens(enviar_agora):
            print("\n" + mensagem["content"])
            for cartao in mensagem["embeds"]:
                print(f"\n== {cartao['title']}\n{cartao['description']}")
        return

    webhook = ARQUIVO_WEBHOOK.read_text().strip()
    for mensagem in montar_mensagens(enviar_agora):
        enviar(webhook, mensagem)
    banco.executemany(
        "INSERT OR IGNORE INTO vistas (id, fonte, titulo, empresa, url) VALUES (?, ?, ?, ?, ?)",
        [(v["id"], v["fonte"], v["titulo"], v["empresa"], v["url"]) for v in enviar_agora],
    )
    banco.commit()


if __name__ == "__main__":
    main()
