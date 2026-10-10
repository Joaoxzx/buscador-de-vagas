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
            contratos = [c for c in ("CLT", "PJ", "Estágio") if c.lower() in etiquetas]
            vagas.append({
                "id": f"github:{repo}#{item['number']}",
                "titulo": titulo,
                "empresa": repo,
                "local": "Remoto" if remoto else "Franca",
                "tipo": ", ".join(contratos) or "não informado",
                "url": item["html_url"],
                "fonte": "GitHub",
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
    # títulos vêm de fora: tira marcação do Discord para não quebrar a mensagem
    return re.sub(r"[*_`~|>@]", "", texto)


def montar_mensagens(vagas):
    blocos = [
        f"**{limpar(v['titulo'])}**\n{limpar(v['empresa'])} · {v['local']} · {v['tipo']} · {v['fonte']}\n<{v['url']}>"
        for v in vagas
    ]
    mensagens, atual = [], f"**{len(vagas)} vaga(s) nova(s)**"
    for bloco in blocos:
        if len(atual) + len(bloco) + 2 > 1900:
            mensagens.append(atual)
            atual = bloco
        else:
            atual += "\n\n" + bloco
    mensagens.append(atual)
    return mensagens


def enviar(webhook, texto):
    corpo = json.dumps({"content": texto, "allowed_mentions": {"parse": []}}).encode("utf-8")
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
    novas.sort(key=lambda v: not NIVEL_JUNIOR.search(v["titulo"]))  # júnior e estágio primeiro
    enviar_agora = novas[:MAX_POR_EXECUCAO]
    print(f"{len(encontradas)} vagas no filtro, {len(novas)} novas, {len(enviar_agora)} para enviar agora.")

    if not enviar_agora:
        return
    if teste:
        for mensagem in montar_mensagens(enviar_agora):
            print("\n" + mensagem)
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
