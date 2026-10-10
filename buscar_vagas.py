#!/usr/bin/env python3
"""Busca vagas novas na Gupy e nos repositórios de vagas do GitHub e manda no #vagas.

Só script, sem IA. As vagas já enviadas ficam em vagas.db para não repetir.
Uso: buscar_vagas.py           -> busca e manda no Discord
     buscar_vagas.py --teste   -> só mostra no terminal, não manda nem marca como vista
"""
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
MAX_POR_EXECUCAO = 15  # o que passar disso fica para a próxima execução

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

# Título com nível acima de júnior fica de fora
NIVEL_ALTO = re.compile(
    r"\b(pleno|pl|s[êe]nior|sr|especialista|lead|l[íi]der|coordenador|gerente|staff|arquitet[oa]|ii|iii)\b",
    re.I,
)
# Título precisa ser de desenvolvimento
DESENVOLVIMENTO = re.compile(
    r"desenvolv|developer|programador|engenheir[oa] de software|software engineer|back.?end|full.?stack|est[áa]gi",
    re.I,
)
# Vaga só de front-end, mobile, QA, design ou suporte fica de fora (full stack e back-end ficam)
FORA_DA_AREA = re.compile(
    r"front.?end|mobile|android|\bios\b|flutter|react native|\bqa\b|design|\bux\b|suporte|\bsap\b|salesforce",
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
    outra = OUTRA_STACK.search(texto)
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
    PASTA.mkdir(parents=True, exist_ok=True)
    banco = abrir_banco()

    encontradas = {v["id"]: v for v in buscar_gupy() + buscar_github()}
    vistas = {linha[0] for linha in banco.execute("SELECT id FROM vistas")}
    novas = [v for i, v in encontradas.items() if i not in vistas]
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
