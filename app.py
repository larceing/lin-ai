"""Lin-AI · prueba de campo.

Servidor FastAPI en http://127.0.0.1:8010. Uso: iniciar.bat
- La IA atiende: interpreta lo que se pide y propone un plato de la carta (carta/).
- El código cocina: consulta A3/Innertia en solo lectura (bd/) y crea Excel o Word (informes/).
- Fichas de negocio, mensajes y consumo en SQLite (datos.db).
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from openai import BadRequestError, NotFoundError, OpenAI, PermissionDeniedError
from pydantic import BaseModel

import contexto
import textos

AQUI = Path(__file__).resolve().parent
# La clave de OpenAI se toma del primer .env que la tenga (o de BOT_ENV).
# Solo se lee la clave (OPENAI_API_KEY o API_OPENAI); la clave nunca se copia ni se muestra.
DOCS = Path(r"C:\Users\karin\Documents")
CANDIDATOS = ([Path(os.environ["BOT_ENV"])] if os.environ.get("BOT_ENV") else []) + [
    AQUI / ".env",
    DOCS / "PROYECTO_LUIS_HENRY_EMPRESA" / "Sistema_Compras_IA" / ".env",
    DOCS / "PROYECTO_LUIS_HENRY_EMPRESA" / "Sistema_Compras_IA" / "Motor_Analisis" / ".env",
    DOCS / "PROYECTO_IA" / ".env",
]
NOMBRES_CLAVE = ("OPENAI_API_KEY", "API_OPENAI", "OPENAI_KEY")
ORIGEN_CLAVE = "variable de entorno" if os.environ.get("OPENAI_API_KEY") else None
for env in CANDIDATOS:
    if ORIGEN_CLAVE:
        break
    if env.is_file():
        valores = dotenv_values(env)
        clave = next((valores[n].strip() for n in NOMBRES_CLAVE if (valores.get(n) or "").strip()), "")
        if clave:
            os.environ["OPENAI_API_KEY"] = clave
            ORIGEN_CLAVE = str(env)
# Gobernanza (standards §4.11.e): Lin-AI debería tener su propia clave en el .env de esta carpeta.
CLAVE_DEDICADA = ORIGEN_CLAVE in ("variable de entorno", str(AQUI / ".env"))
# El resto del .env de esta carpeta (motores, topes, correo) pasa a variables de entorno.
# Las credenciales de la BD las lee bd/lectura.py directamente del .env.
load_dotenv(AQUI / ".env")

# Gobernanza (standards §4.11): solo modelos aprobados, sin cascada a otros.
APROBADOS = ("gpt-5.4-mini", "gpt-4.1")
# Motores: cada uso tiene su modelo. Se pueden cambiar en el .env (MOTOR_RAPIDO, MOTOR_PRECISO),
# pero solo a un modelo aprobado.
MOTORES = {
    "rapido": os.environ.get("MOTOR_RAPIDO") or os.environ.get("OPENAI_MODEL") or "gpt-5.4-mini",
    "preciso": os.environ.get("MOTOR_PRECISO") or "gpt-4.1",
}
for _motor, _modelo in list(MOTORES.items()):
    if _modelo not in APROBADOS:
        print(f"AVISO: {_modelo} no está aprobado; el motor {_motor} usa {APROBADOS[0]}")
        MOTORES[_motor] = APROBADOS[0]
# Tope de gasto mensual (USD) y de longitud de respuesta (tokens).
LIMITE_MENSUAL_USD = float(os.environ.get("LIMITE_MENSUAL_USD") or 10)
MAX_TOKENS_RESPUESTA = int(os.environ.get("MAX_TOKENS_RESPUESTA") or 800)
# USD por millón de tokens (entrada, salida, entrada en caché), precios oficiales de OpenAI
# consultados el 08/10/2026 (developers.openai.com/api/docs/models). Si no se conoce el precio
# de un modelo se usa el de gpt-4.1, que es de los caros: así el tope nunca se queda corto.
# Se ajusta en el .env con PRECIO_GPT_5_4_MINI=entrada/salida/caché (p. ej. 0.75/4.50/0.075).
PRECIOS = {"gpt-5.4-mini": (0.75, 4.50, 0.075), "gpt-4.1": (2.00, 8.00, 0.50)}
for _modelo in APROBADOS:
    _env = os.environ.get("PRECIO_" + _modelo.upper().replace("-", "_").replace(".", "_"))
    if _env:
        _p = [float(x) for x in _env.split("/")]
        PRECIOS[_modelo] = (_p[0], _p[1], _p[2] if len(_p) > 2 else _p[0])
# Razonamiento de los modelos gpt-5: "low" gasta muchos menos tokens de salida que el de serie.
RAZONAMIENTO = (os.environ.get("RAZONAMIENTO") or "low").strip().lower()
# Mensajes anteriores de la conversación que se reenvían en cada pregunta.
HISTORIAL = int(os.environ.get("HISTORIAL_MENSAJES") or 6)
BD = AQUI / "datos.db"
SISTEMA = (
    "Eres Lin-AI, el asistente interno de SPSIL (empresa que usa A3ERP y Power BI). "
    "Responde en español, claro y breve, en lenguaje de negocio: sin SQL, tablas internas "
    "ni nombres técnicos. Para explicar cómo se calcula algo, usa SOLO la ficha de negocio "
    "de abajo y pon un ejemplo numérico sencillo si ayuda. Si no lo cubre, dilo, no lo "
    "inventes, y di qué ficha de la lista podría servir."
)

app = FastAPI(title="Lin-AI prueba de campo")
cliente = None


def bd():
    con = sqlite3.connect(BD)
    con.row_factory = sqlite3.Row
    return con


def ahora():
    return datetime.now().isoformat(timespec="seconds")


@app.on_event("startup")
def preparar():
    global cliente
    with closing(bd()) as con, con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS fichas (
                id INTEGER PRIMARY KEY, nombre TEXT UNIQUE, contenido TEXT,
                actualizado TEXT, autor TEXT);
            CREATE TABLE IF NOT EXISTS mensajes (
                id INTEGER PRIMARY KEY, conversacion TEXT, rol TEXT,
                contenido TEXT, fecha TEXT);
            CREATE TABLE IF NOT EXISTS consumo (
                id INTEGER PRIMARY KEY, fecha TEXT, motor TEXT, modelo TEXT,
                tokens_entrada INTEGER, tokens_salida INTEGER, coste_usd REAL);
        """)
        columnas = [c[1] for c in con.execute("PRAGMA table_info(consumo)")]
        if "tokens_cache" not in columnas:
            # Primera vez con precios reales: se añade la columna de caché y se recalcula lo
            # ya gastado, que se había apuntado a precio de gpt-4.1.
            con.execute("ALTER TABLE consumo ADD COLUMN tokens_cache INTEGER DEFAULT 0")
            for f in con.execute("SELECT id, modelo, tokens_entrada, tokens_salida FROM consumo").fetchall():
                con.execute("UPDATE consumo SET coste_usd=? WHERE id=?",
                            (coste(f["modelo"], f["tokens_entrada"], f["tokens_salida"]), f["id"]))
        # Carga inicial: cada .md de fichas/ entra una vez como ficha
        for md in sorted((AQUI / "fichas").glob("*.md")):
            con.execute(
                "INSERT OR IGNORE INTO fichas (nombre, contenido, actualizado, autor) VALUES (?,?,?,?)",
                (md.stem, md.read_text(encoding="utf-8"), ahora(), "carga inicial"))
    from bd import lectura
    for origen, e in lectura.estado().items():
        print(f"BD {e['nombre']}: " + ("credenciales en el .env (se verifica solo lectura al primer uso)"
                                      if e["credenciales_en_env"] else "sin credenciales en el .env"))
    if ORIGEN_CLAVE:
        # Nunca esperar a OpenAI más de 1 minuto (y como mucho un reintento)
        cliente = OpenAI(timeout=60, max_retries=1)
        print(f"Clave de OpenAI cargada desde: {ORIGEN_CLAVE}")
        if not CLAVE_DEDICADA:
            print("AVISO de gobernanza: esta clave es de otro sistema. Crea una clave solo para")
            print("Lin-AI y ponla como OPENAI_API_KEY=... en el .env de esta carpeta.")
    else:
        print("AVISO: no encuentro OPENAI_API_KEY en ninguno de estos .env:")
        for env in CANDIDATOS:
            print(f"   - {env}{'' if env.is_file() else '  (no existe)'}")
        print("Pon una linea OPENAI_API_KEY=... en el .env de esta carpeta.")
    print(f"Lin-AI listo en http://127.0.0.1:8010 · motores {MOTORES}")
    print(f"Tope de gasto: {LIMITE_MENSUAL_USD:.2f} USD/mes · gastado este mes: {gasto_mes():.4f} USD")


class Pregunta(BaseModel):
    conversacion: str
    mensaje: str
    motor: str = "rapido"
    idioma: str = "es"  # "es", "zh" o "en"


class Ficha(BaseModel):
    contenido: str
    autor: str = "anónimo"


@app.get("/")
def inicio():
    return FileResponse(AQUI / "index.html")


@app.post("/api/apagar")
def apagar(request: Request):
    """Apaga Lin-AI del todo. Solo desde este mismo equipo y con la cabecera X-Lin-AI: apagar."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1")             or request.headers.get("X-Lin-AI") != "apagar":
        raise HTTPException(403, "No permitido")
    import threading
    print("Lin-AI apagado desde la página.")
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


@app.post("/api/chat")
def chat(p: Pregunta):
    id_ = textos.idioma(p.idioma)
    if cliente is None:
        print("Falta la clave de OpenAI en el .env")
        raise HTTPException(503, textos.t("sin_clave", id_))
    if p.motor not in MOTORES:
        raise HTTPException(400, f"Motor desconocido: {p.motor}. Disponibles: {', '.join(MOTORES)}")
    if gasto_mes() >= LIMITE_MENSUAL_USD:
        raise HTTPException(429, textos.t("tope", id_))
    with closing(bd()) as con, con:
        fichas = con.execute("SELECT nombre, contenido FROM fichas ORDER BY nombre").fetchall()
        previos = con.execute(
            "SELECT rol, contenido FROM mensajes WHERE conversacion=? ORDER BY id DESC LIMIT ?",
            (p.conversacion, HISTORIAL)).fetchall()[::-1]
        # Solo la ficha que encaja con la pregunta (y con la anterior, por si es un "¿y eso?")
        tema = " ".join([m["contenido"] for m in previos if m["rol"] == "user"][-1:] + [p.mensaje])
        mensajes = [{"role": "system", "content": SISTEMA + "\n\n" + contexto.bloque(fichas, tema)
                     + textos.instruccion_ia(id_)}]
        mensajes += [{"role": m["rol"], "content": m["contenido"]} for m in previos]
        mensajes.append({"role": "user", "content": p.mensaje})
        try:
            r = preguntar(mensajes, MOTORES[p.motor])
        except Exception as e:
            print(f"OpenAI no respondió: {e}")
            raise HTTPException(502, textos.t("ia_no_responde", id_))
        respuesta = r.choices[0].message.content
        registrar_consumo(con, p.motor, r)
        con.executemany(
            "INSERT INTO mensajes (conversacion, rol, contenido, fecha) VALUES (?,?,?,?)",
            [(p.conversacion, "user", p.mensaje, ahora()),
             (p.conversacion, "assistant", respuesta, ahora())])
    return {"respuesta": respuesta}


def preguntar(mensajes, modelo, **extra):
    """Llama a OpenAI con el modelo del motor; si no está disponible, prueba el otro aprobado."""
    ultimo = None
    for m in [modelo] + [x for x in APROBADOS if x != modelo]:
        try:
            if m.startswith("gpt-5") and RAZONAMIENTO:
                try:
                    return cliente.chat.completions.create(
                        model=m, messages=mensajes, max_completion_tokens=MAX_TOKENS_RESPUESTA,
                        reasoning_effort=RAZONAMIENTO, **extra)
                except BadRequestError as e:
                    if "reasoning" not in str(e).lower():
                        raise
                    print(f"AVISO: {m} no acepta RAZONAMIENTO={RAZONAMIENTO}; sigo sin él.")
            return cliente.chat.completions.create(
                model=m, messages=mensajes, max_completion_tokens=MAX_TOKENS_RESPUESTA, **extra)
        except (NotFoundError, PermissionDeniedError, BadRequestError) as e:
            if "model" not in str(e).lower():
                raise
            print(f"Modelo {m} no disponible en este proyecto de OpenAI, pruebo otro aprobado...")
            ultimo = e
    raise RuntimeError(f"Ningún modelo aprobado ({', '.join(APROBADOS)}) está disponible: {ultimo}")


def coste(modelo, entrada, salida, cache=0):
    # r.model puede traer fecha (gpt-4.1-2025-04-14): se busca el precio por prefijo.
    # Los tokens de entrada que OpenAI sirve de su caché cuestan una décima parte.
    base = next((m for m in sorted(PRECIOS, key=len, reverse=True) if modelo.startswith(m)), "gpt-4.1")
    pe, ps, pc = PRECIOS[base]
    return ((entrada - cache) * pe + cache * pc + salida * ps) / 1_000_000


def registrar_consumo(con, motor, r):
    u = r.usage
    entrada, salida = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
    detalle = getattr(u, "prompt_tokens_details", None) if u else None
    cache = getattr(detalle, "cached_tokens", 0) or 0
    con.execute(
        "INSERT INTO consumo (fecha, motor, modelo, tokens_entrada, tokens_salida, tokens_cache, "
        "coste_usd) VALUES (?,?,?,?,?,?,?)",
        (ahora(), motor, r.model, entrada, salida, cache, coste(r.model, entrada, salida, cache)))


def gasto_mes():
    with closing(bd()) as con:
        fila = con.execute("SELECT COALESCE(SUM(coste_usd), 0) FROM consumo WHERE fecha >= ?",
                           (datetime.now().strftime("%Y-%m-01"),)).fetchone()
    return fila[0]


@app.get("/api/consumo")
def consumo():
    with closing(bd()) as con:
        filas = con.execute(
            "SELECT substr(fecha,1,7) AS mes, motor, modelo, COUNT(*) AS preguntas, "
            "SUM(tokens_entrada) AS tokens_entrada, SUM(tokens_cache) AS tokens_cache, "
            "SUM(tokens_salida) AS tokens_salida, "
            "ROUND(SUM(coste_usd), 4) AS coste_usd FROM consumo GROUP BY mes, motor, modelo "
            "ORDER BY mes DESC").fetchall()
    return {"motores": MOTORES, "limite_mensual_usd": LIMITE_MENSUAL_USD,
            "gastado_este_mes_usd": round(gasto_mes(), 4), "detalle": [dict(f) for f in filas]}


@app.get("/api/fichas")
def listar_fichas():
    with closing(bd()) as con:
        return [dict(f) for f in con.execute(
            "SELECT nombre, contenido, actualizado, autor FROM fichas ORDER BY nombre")]


@app.put("/api/fichas/{nombre}")
def guardar_ficha(nombre: str, f: Ficha):
    with closing(bd()) as con, con:
        con.execute(
            "INSERT INTO fichas (nombre, contenido, actualizado, autor) VALUES (?,?,?,?) "
            "ON CONFLICT(nombre) DO UPDATE SET contenido=excluded.contenido, "
            "actualizado=excluded.actualizado, autor=excluded.autor",
            (nombre, f.contenido, ahora(), f.autor))
    return {"ok": True}


@app.get("/api/historial")
def historial(limite: int = 100):
    with closing(bd()) as con:
        return [dict(m) for m in con.execute(
            "SELECT conversacion, rol, contenido, fecha FROM mensajes ORDER BY id DESC LIMIT ?",
            (limite,))]


# --------------------------------------------------------------------------- IA para los módulos
def _ia(mensajes, motor_registro, **extra):
    """Toda llamada a la IA pasa por aquí: modelos aprobados, tope de gasto y registro de consumo."""
    if cliente is None:
        print("Falta la clave de OpenAI en el .env")
        raise HTTPException(503, textos.t("sin_clave"))
    if gasto_mes() >= LIMITE_MENSUAL_USD:
        raise HTTPException(429, textos.t("tope"))
    try:
        r = preguntar(mensajes, MOTORES["rapido"], **extra)
    except Exception as e:
        print(f"OpenAI no respondió: {e}")
        raise HTTPException(502, textos.t("ia_no_responde"))
    with closing(bd()) as con, con:
        registrar_consumo(con, motor_registro, r)
    return r.choices[0].message


def completar(mensajes, herramientas):
    """La IA debe elegir una herramienta (proponer, preguntar, responder...)."""
    return _ia(mensajes, "carta", tools=herramientas, tool_choice="required")


def completar_informes(mensajes, herramientas):
    return _ia(mensajes, "informes", tools=herramientas)


def llamar_ia(mensajes):
    return _ia(mensajes, "informes").content


def texto_fichas(tema):
    with closing(bd()) as con:
        fichas = con.execute("SELECT nombre, contenido FROM fichas ORDER BY nombre").fetchall()
    return contexto.bloque_pedido(fichas, tema)


from bd.rutas import router as router_bd  # noqa: E402
from carta.rutas import crear_router as router_carta  # noqa: E402
from informes.rutas import crear_router as router_informes  # noqa: E402

app.include_router(router_bd)
app.include_router(router_informes(AQUI, llamar_ia, completar_informes))
app.include_router(router_carta(AQUI, BD, completar, texto_fichas))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8010)
