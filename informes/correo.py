"""Lin-AI · envío de informes por el correo de desarrollo de IONOS (SMTP).

Reglas:
- Las credenciales solo se leen del .env (SMTP_USUARIO, SMTP_CLAVE); nunca se imprimen.
  Si el .env de Lin-AI no las tiene, se toman del .env del Motor_Analisis (el que ya envía
  informes por IONOS): SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM y
  MAIL_TO_DEFAULT. Ruta cambiable con CORREO_ENV.
- Solo se envía a destinatarios de la lista CORREO_PERMITIDOS del .env
  (direcciones completas o dominios con @, p. ej. "luis@spsil.es,@spsil.es").
  Si no hay lista, solo se permite MAIL_TO_DEFAULT del Motor_Analisis.
- Enviar son dos pasos: preparar un borrador (devuelve la vista previa) y confirmarlo.
  El borrador caduca a los 30 minutos.
- Mientras CORREO_ENVIO_REAL no sea "si", al confirmar se guarda un .eml en la carpeta
  de salida en vez de mandarlo. Así se puede probar todo sin enviar nada.
- El cuerpo lo monta plantilla.py: cabecera SPSIL, nota breve opcional y una tarjeta por
  adjunto con su resumen calculado por código. Quien llama solo da un título y una nota.

Probar la conexión (solo inicia sesión, no envía nada):
    python correo.py --probar
"""
import mimetypes
import os
import re
import smtplib
import sqlite3
import ssl
import sys
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path

try:
    from . import plantilla
except ImportError:  # al ejecutar "python correo.py --probar"
    import plantilla

CADUCIDAD = timedelta(minutes=30)
MAX_ADJUNTOS_MB = 15


class ErrorCorreo(Exception):
    """Error que se puede mostrar tal cual al usuario (nunca lleva la clave)."""


# .env del Motor_Analisis de Sistema_Compras_IA: ya tiene el buzón de IONOS configurado.
ENV_MOTOR = Path(os.environ.get("CORREO_ENV") or
                 r"C:\Users\karin\Documents\PROYECTO_LUIS_HENRY_EMPRESA\Sistema_Compras_IA\Motor_Analisis\.env")
CLAVES_MOTOR = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO_DEFAULT")


def _limpia_valor(v):
    """Mismo criterio que motor.py: quita comentarios ' # ...' y '; <-- ...' sin romper claves."""
    v = re.split(r"\s+#", v, 1)[0]
    v = re.split(r";(?=\s|<|$)", v, 1)[0]
    return v.strip().strip('"').strip("'")


def _env_motor():
    """Lee solo las variables de correo del .env del Motor_Analisis (si existe)."""
    valores = {}
    try:
        with open(ENV_MOTOR, encoding="utf-8-sig") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith(("#", ";")) or "=" not in linea:
                    continue
                k, v = linea.split("=", 1)
                if k.strip() in CLAVES_MOTOR:
                    valores[k.strip()] = _limpia_valor(v)
    except OSError:
        pass
    return valores


def _conexion_txt():
    """conexion_bd.txt (junto a app.py): ahí la persona puede poner CORREO_PERMITIDOS sin tocar el .env."""
    valores = {}
    try:
        with open(Path(__file__).resolve().parent.parent / "conexion_bd.txt", encoding="utf-8-sig") as f:
            for linea in f:
                linea = linea.strip()
                if linea.startswith("CORREO_") and "=" in linea:
                    k, v = linea.split("=", 1)
                    valores[k.strip()] = _limpia_valor(v)
    except OSError:
        pass
    return valores


def config():
    m = _env_motor()
    e = lambda nombre: (os.environ.get(nombre) or "").strip()
    usuario = e("SMTP_USUARIO") or m.get("SMTP_USER", "")
    remitente = e("SMTP_REMITENTE") or m.get("MAIL_FROM", "") or usuario
    lista = e("CORREO_PERMITIDOS") or _conexion_txt().get("CORREO_PERMITIDOS", "")
    permitidos = [x.strip().lower() for x in lista.split(",") if x.strip()]
    if not permitidos and m.get("MAIL_TO_DEFAULT"):
        permitidos = [m["MAIL_TO_DEFAULT"].strip().lower()]
    try:
        puerto = int(e("SMTP_PUERTO") or m.get("SMTP_PORT") or 587)
    except ValueError:
        puerto = 587
    return {
        "host": e("SMTP_HOST") or m.get("SMTP_HOST") or "smtp.ionos.es",
        "puerto": puerto,
        "usuario": usuario,
        "clave": os.environ.get("SMTP_CLAVE") or m.get("SMTP_PASSWORD", ""),
        "remitente": remitente,
        "permitidos": permitidos,
        "real": e("CORREO_ENVIO_REAL").lower() in ("si", "sí", "1", "true"),
        "origen": "Lin-AI" if e("SMTP_USUARIO") else (str(ENV_MOTOR) if m else "ninguno"),
    }


def _limpia_direccion(texto):
    nombre, direccion = parseaddr(texto or "")
    direccion = direccion.strip().lower()
    if not direccion or "@" not in direccion or any(c in direccion for c in "\r\n ,;"):
        raise ErrorCorreo(f"Dirección no válida: {texto!r}")
    return direccion


def destinatarios_validos(lista, permitidos):
    if not permitidos:
        raise ErrorCorreo("No hay destinatarios permitidos. Pon CORREO_PERMITIDOS=... en el .env.")
    limpios = []
    for d in lista:
        d = _limpia_direccion(d)
        dominio = "@" + d.split("@", 1)[1]
        if d not in permitidos and dominio not in permitidos:
            raise ErrorCorreo(f"{d} no está en la lista de destinatarios permitidos.")
        if d not in limpios:
            limpios.append(d)
    if not limpios:
        raise ErrorCorreo("Falta al menos un destinatario.")
    return limpios


def _bd(ruta_bd):
    con = sqlite3.connect(ruta_bd)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS correos (
        id TEXT PRIMARY KEY, creado TEXT, para TEXT, asunto TEXT, cuerpo TEXT,
        adjuntos TEXT, estado TEXT, enviado TEXT, detalle TEXT, autor TEXT)""")
    if "html" not in {f[1] for f in con.execute("PRAGMA table_info(correos)")}:
        con.execute("ALTER TABLE correos ADD COLUMN html TEXT")  # bases creadas antes de la plantilla
    return con


def preparar(ruta_bd, para, asunto, cuerpo, adjuntos=(), autor="anónimo"):
    """Valida y guarda un borrador. No envía nada. Devuelve la vista previa.

    `asunto` es solo el título (el código añade "[Lin-AI SPSIL]" y la fecha; si falta, sale
    del primer adjunto). `cuerpo` es una nota breve opcional en texto plano: el saludo, el
    resumen de los adjuntos y el pie los pone la plantilla."""
    cfg = config()
    para = destinatarios_validos(para, cfg["permitidos"])
    rutas = [Path(a) for a in adjuntos]
    for r in rutas:
        if not r.is_file():
            raise ErrorCorreo(f"No encuentro el adjunto {r.name}.")
    if sum(r.stat().st_size for r in rutas) > MAX_ADJUNTOS_MB * 1024 * 1024:
        raise ErrorCorreo(f"Los adjuntos pasan de {MAX_ADJUNTOS_MB} MB.")
    fichas = [plantilla.resumir_adjunto(r) for r in rutas]
    try:
        nota = plantilla.limpiar_nota(cuerpo, fichas)
    except plantilla.NotaNoValida as e:
        raise ErrorCorreo(str(e)) from None
    titulo = plantilla.limpiar_titulo(asunto, fichas)
    asunto = plantilla.asunto(titulo)
    texto, html = plantilla.componer(titulo, nota, fichas, autor, modo_prueba=not cfg["real"])
    ident = uuid.uuid4().hex[:12]
    with closing(_bd(ruta_bd)) as con, con:
        con.execute("INSERT INTO correos (id, creado, para, asunto, cuerpo, adjuntos, estado, autor, html) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (ident, datetime.now().isoformat(timespec="seconds"), ",".join(para), asunto,
                     texto, "\n".join(str(r) for r in rutas), "borrador", autor, html))
    return {"id": ident, "de": cfg["remitente"] or "(falta SMTP_REMITENTE)", "para": para,
            "asunto": asunto, "cuerpo": texto, "resumen_adjuntos": fichas,
            "vista_previa": f"/api/correo/{ident}/vista", "adjuntos": [r.name for r in rutas],
            "modo": "envío real" if cfg["real"] else "prueba (se guarda un .eml, no se envía)",
            "caduca_en_minutos": int(CADUCIDAD.total_seconds() // 60)}


def _mensaje(cfg, fila):
    msg = EmailMessage()
    msg["From"] = formataddr(("Lin-AI SPSIL", cfg["remitente"]))
    msg["To"] = ", ".join(fila["para"].split(","))
    msg["Subject"] = fila["asunto"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(cfg["remitente"].split("@")[-1] or None))
    msg.set_content(fila["cuerpo"] or "")
    if fila["html"]:
        msg.add_alternative(fila["html"], subtype="html")
    for linea in (fila["adjuntos"] or "").splitlines():
        r = Path(linea)
        tipo = mimetypes.guess_type(r.name)[0] or "application/octet-stream"
        principal, sub = tipo.split("/", 1)
        msg.add_attachment(r.read_bytes(), maintype=principal, subtype=sub, filename=r.name)
    return msg


def _abrir_smtp(cfg):
    if not cfg["usuario"] or not cfg["clave"]:
        raise ErrorCorreo("Faltan el usuario o la clave del correo (ni en el .env de Lin-AI "
                          "ni en el del Motor_Analisis).")
    contexto = ssl.create_default_context()
    try:
        if cfg["puerto"] == 465:
            s = smtplib.SMTP_SSL(cfg["host"], cfg["puerto"], context=contexto, timeout=30)
        else:
            s = smtplib.SMTP(cfg["host"], cfg["puerto"], timeout=30)
            s.ehlo()
            s.starttls(context=contexto)
            s.ehlo()
        s.login(cfg["usuario"], cfg["clave"])
        return s
    except smtplib.SMTPAuthenticationError:
        raise ErrorCorreo("IONOS rechazó el usuario o la clave del correo (revisa el .env).") from None
    except (OSError, smtplib.SMTPException) as e:
        raise ErrorCorreo(f"No pude conectar con {cfg['host']}:{cfg['puerto']} ({type(e).__name__}).") from None


def confirmar(ruta_bd, ident, carpeta_salida):
    """Envía (o guarda como .eml en modo prueba) un borrador preparado antes."""
    cfg = config()
    with closing(_bd(ruta_bd)) as con, con:
        fila = con.execute("SELECT * FROM correos WHERE id=?", (ident,)).fetchone()
        if not fila:
            raise ErrorCorreo("Ese borrador no existe.")
        if fila["estado"] != "borrador":
            raise ErrorCorreo(f"Ese borrador ya está {fila['estado']}.")
        if datetime.now() - datetime.fromisoformat(fila["creado"]) > CADUCIDAD:
            con.execute("UPDATE correos SET estado='caducado' WHERE id=?", (ident,))
            raise ErrorCorreo("El borrador ha caducado; prepáralo otra vez.")
        # Se vuelve a comprobar la lista por si el .env cambió desde que se preparó
        destinatarios_validos(fila["para"].split(","), cfg["permitidos"])
        if not cfg["remitente"]:
            raise ErrorCorreo("Falta SMTP_REMITENTE (o SMTP_USUARIO) en el .env.")
        msg = _mensaje(cfg, fila)
        if cfg["real"]:
            with _abrir_smtp(cfg) as s:
                s.send_message(msg)
            estado, detalle = "enviado", f"{cfg['host']}:{cfg['puerto']}"
        else:
            eml = Path(carpeta_salida) / f"correo_{ident}.eml"
            eml.parent.mkdir(parents=True, exist_ok=True)
            eml.write_bytes(bytes(msg))
            estado, detalle = "simulado", eml.name
        con.execute("UPDATE correos SET estado=?, enviado=?, detalle=? WHERE id=?",
                    (estado, datetime.now().isoformat(timespec="seconds"), detalle, ident))
    return {"id": ident, "estado": estado, "detalle": detalle, "para": fila["para"].split(",")}


def vista(ruta_bd, ident):
    """HTML del borrador tal y como lo verá quien lo reciba."""
    with closing(_bd(ruta_bd)) as con:
        fila = con.execute("SELECT html, cuerpo FROM correos WHERE id=?", (ident,)).fetchone()
    if not fila:
        raise ErrorCorreo("Ese borrador no existe.")
    return fila["html"] or f"<pre>{plantilla.html.escape(fila['cuerpo'] or '')}</pre>"


def probar_conexion():
    """Inicia sesión en IONOS y cierra. No envía nada."""
    cfg = config()
    with _abrir_smtp(cfg):
        pass
    return (f"Conexión correcta con {cfg['host']}:{cfg['puerto']} como {cfg['usuario']} "
            f"(configuración tomada de: {cfg['origen']})")


if __name__ == "__main__" and "--probar" in sys.argv:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # el .env de Lin-AI, junto a app.py
    try:
        print(probar_conexion())
    except ErrorCorreo as e:
        print(f"ERROR: {e}")
        sys.exit(1)
