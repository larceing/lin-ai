"""Lin-AI · acceso a bases de datos en SOLO LECTURA.

Orígenes:
  - "a3"        A3ERP en SQL Server 2017 (por defecto 192.168.10.209, BD SG)
  - "innertia"  Innertia en MariaDB (por defecto 192.168.10.215:3306, BD almacenmultiforte)

Invariantes (no configurables, el código falla cerrado si alguno no se cumple):
  I1. Solo se ejecuta UNA sentencia SELECT (o WITH ... SELECT). Nada de INSERT, UPDATE,
      DELETE, MERGE, ALTER, CREATE, DROP, TRUNCATE, EXEC, SELECT INTO, comentarios, etc.
  I2. El usuario de BD debe ser de solo lectura: al conectar se comprueban sus permisos y, si
      puede escribir, ese origen queda bloqueado (no se ejecuta ninguna consulta).
  I3. Cada consulta corre en una transacción que se deshace siempre (ROLLBACK). En MariaDB la
      sesión además se marca READ ONLY, así que el propio servidor rechaza cualquier escritura.
  I4. Las credenciales salen solo de archivos de configuración locales (conexion_bd.txt, .env,
      o el .env del Motor_Analisis como respaldo) o de variables de entorno; nunca se imprimen
      ni se devuelven en errores.
  I5. Toda consulta queda registrada (quién, qué, cuántas filas, si falló) en bd_auditoria.jsonl.
  I6. Límite de filas (500; 20.000 para Excel) y de tiempo por consulta, para no cargar el ERP.
"""
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = lambda _p: {}

AQUI = Path(__file__).resolve().parent
APP = AQUI.parent  # carpeta de la app (donde está app.py y su .env)
AUDITORIA = APP / "bd_auditoria.jsonl"


# --------------------------------------------------------------------------- configuración
# Respaldo: el .env del Motor_Analisis de Sistema_Compras_IA ya tiene la conexión a las BD.
ENV_RESPALDO = Path(os.environ.get("BD_ENV_RESPALDO") or
                    r"C:\Users\karin\Documents\PROYECTO_LUIS_HENRY_EMPRESA\Sistema_Compras_IA\Motor_Analisis\.env")
MAPEO_RESPALDO = {
    "DB_SERVER": "A3_SERVIDOR", "DB_DATABASE": "A3_BD", "DB_USER": "A3_USUARIO",
    "DB_PASSWORD": "A3_CLAVE", "DB_TRUSTED": "A3_INTEGRADA",
    "MARIADB_HOST": "INN_SERVIDOR", "MARIADB_PORT": "INN_PUERTO", "MARIADB_DATABASE": "INN_BD",
    "MARIADB_USER": "INN_USUARIO", "MARIADB_PASSWORD": "INN_CLAVE",
}
# Valores de ejemplo que no cuentan como configurados
RELLENO = {"cambia-esto", "cambiaesto", "cambiar", "changeme", "xxxx", "****", "todo", "none", "null"}


def _limpia_valor(v):
    """Como motor.py: quita comentarios ' # ...' y '; <-- ...' de la línea sin romper claves."""
    v = re.split(r"\s+#", v, 1)[0]
    v = re.split(r";(?=\s|<|$)", v, 1)[0]
    return v.strip().strip('"').strip("'")


def _leer(ruta, mapeo=None, sin_relleno=False):
    """Lee un archivo clave=valor tolerante a comentarios. Los valores vacíos no cuentan.
    Con sin_relleno (solo el respaldo) tampoco cuentan los valores de ejemplo: en los archivos
    que rellena la persona, cualquier valor es real aunque se parezca a uno de ejemplo."""
    valores = {}
    try:
        with open(ruta, encoding="utf-8-sig") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith(("#", ";")) or "=" not in linea:
                    continue
                k, v = (x.strip() for x in linea.split("=", 1))
                if mapeo is not None:
                    if k not in mapeo:
                        continue
                    k = mapeo[k]
                v = _limpia_valor(v)
                if v and not (sin_relleno and (v.lower() in RELLENO or re.fullmatch(r"<.*>", v))):
                    valores[k] = v
    except OSError:
        pass
    return valores


def _config():
    """De menos a más prioridad: respaldo del Motor_Analisis, .env de la app (o BOT_ENV),
    conexion_bd.txt (lo que rellena la persona) y variables de entorno. Un valor vacío nunca
    pisa a uno real; los valores de ejemplo solo se descartan en el respaldo."""
    valores = _leer(ENV_RESPALDO, MAPEO_RESPALDO, sin_relleno=True)
    for env in ([Path(os.environ["BOT_ENV"])] if os.environ.get("BOT_ENV") else []) + [APP / ".env"]:
        valores.update(_leer(env))
    valores.update(_leer(APP / "conexion_bd.txt"))
    valores.update({k: v for k, v in os.environ.items() if k.startswith(("A3_", "INN_", "BD_")) and v})
    # Si conexion_bd.txt o el .env ponen usuario y clave de A3, no se usa la autenticación de Windows
    if valores.get("A3_USUARIO") and valores.get("A3_CLAVE"):
        valores.pop("A3_INTEGRADA", None)
    return valores


CFG = _config()
MAX_FILAS = int(CFG.get("BD_MAX_FILAS") or 500)
TIEMPO_MAX_S = int(CFG.get("BD_TIEMPO_MAX_S") or 30)
MAX_FILAS_INFORME = max(MAX_FILAS, int(CFG.get("BD_MAX_FILAS_INFORME") or 20000))

ORIGENES = {
    "a3": {
        "nombre": "A3ERP (SQL Server)",
        "servidor": CFG.get("A3_SERVIDOR") or "192.168.10.209",
        "puerto": CFG.get("A3_PUERTO") or "",
        "bd": CFG.get("A3_BD") or "SG",
        "usuario": CFG.get("A3_USUARIO") or "",
        "clave": CFG.get("A3_CLAVE") or "",
        # Autenticación de Windows (DB_TRUSTED del Motor_Analisis): sin usuario ni clave
        "integrada": (CFG.get("A3_INTEGRADA") or "").strip().lower() in ("si", "sí", "yes", "true", "1"),
    },
    "innertia": {
        "nombre": "Innertia (MariaDB)",
        "servidor": CFG.get("INN_SERVIDOR") or "192.168.10.215",
        "puerto": CFG.get("INN_PUERTO") or "3306",
        "bd": CFG.get("INN_BD") or "almacenmultiforte",
        "usuario": CFG.get("INN_USUARIO") or "",
        "clave": CFG.get("INN_CLAVE") or "",
        "integrada": False,
    },
}


class ErrorBD(Exception):
    """Error que se puede enseñar al usuario: nunca lleva credenciales."""


class ConsultaProhibida(ErrorBD):
    """La consulta no cumple el invariante de solo lectura (I1)."""


# --------------------------------------------------------------------------- I1: validador
# Palabras que nunca pueden aparecer fuera de textos entre comillas. Cubren escritura, DDL,
# ejecución de código, acceso a ficheros/otros servidores, bloqueos y cambios de sesión.
# (REPLACE() como función de texto sí se permite: REPLACE INTO ya cae por INTO.)
PROHIBIDAS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "TRUNCATE",
    "ALTER", "CREATE", "DROP", "RENAME",
    "GRANT", "REVOKE", "DENY",
    "EXEC", "EXECUTE", "CALL", "HANDLER", "PREPARE", "DEALLOCATE",
    "INTO", "OUTFILE", "DUMPFILE", "LOAD", "BULK", "BACKUP", "RESTORE",
    "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML",
    "DBCC", "SHUTDOWN", "KILL", "RECONFIGURE", "CHECKPOINT",
    "USE", "SET", "DECLARE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "TRANSACTION",
    "LOCK", "UNLOCK", "WAITFOR", "SLEEP", "BENCHMARK", "GET_LOCK", "LOAD_FILE",
    "FLUSH", "RESET", "PURGE", "INSTALL", "UNINSTALL", "ANALYZE", "OPTIMIZE", "REPAIR",
    "NEXTVAL", "SETVAL",
)
_PROHIBIDAS_RE = re.compile(r"\b(" + "|".join(PROHIBIDAS) + r")\b", re.IGNORECASE)
# Prefijos de procedimientos del sistema (xp_cmdshell, sp_configure, ...)
_PROC_SISTEMA_RE = re.compile(r"\b(xp_|sp_|fn_dblog|fn_dump)\w*", re.IGNORECASE)
# Textos entre comillas e identificadores entrecomillados: se neutralizan antes de buscar.
_LITERALES_RE = re.compile(
    r"'(?:[^']|'')*'"          # 'texto' (con '' escapado)
    r'|"(?:[^"]|"")*"'         # "identificador"
    r"|\[(?:[^\]]|\]\])*\]"    # [identificador] de SQL Server
    r"|`(?:[^`]|``)*`"         # `identificador` de MariaDB
)


def validar_sql(sql: str) -> str:
    """Devuelve la sentencia limpia si es un SELECT de solo lectura; si no, ConsultaProhibida.

    Es deliberadamente estricto: ante la duda, rechaza. Se aplica a TODAS las consultas,
    también a las del catálogo.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ConsultaProhibida("Consulta vacía.")
    if "\\" in sql:
        # MariaDB interpreta \' dentro de textos; prohibirlo evita engañar al validador.
        raise ConsultaProhibida("No se admite la barra invertida (\\) en la consulta.")
    sql = sql.strip().rstrip(";").strip()
    neutro = _LITERALES_RE.sub(" 0 ", sql)
    if re.search(r"['\"`\[\]]", neutro):
        raise ConsultaProhibida("Comillas o corchetes sin cerrar.")
    if "--" in neutro or "/*" in neutro or "*/" in neutro or "#" in neutro:
        # Sin comentarios: en MariaDB /*! ... */ se EJECUTA, y pueden esconder otra sentencia.
        raise ConsultaProhibida("No se admiten comentarios en la consulta.")
    if ";" in neutro:
        raise ConsultaProhibida("Solo se admite una sentencia (sin ';' intermedios).")
    primera = re.match(r"\s*\(*\s*(\w+)", neutro)
    if not primera or primera.group(1).upper() not in ("SELECT", "WITH"):
        raise ConsultaProhibida("Solo se admiten consultas SELECT (o WITH ... SELECT).")
    m = _PROHIBIDAS_RE.search(neutro)
    if m:
        raise ConsultaProhibida(f"Palabra no permitida en una consulta de lectura: {m.group(1).upper()}.")
    m = _PROC_SISTEMA_RE.search(neutro)
    if m:
        raise ConsultaProhibida(f"No se permiten procedimientos del sistema: {m.group(0)}.")
    return sql


# --------------------------------------------------------------------------- conexión
def _limpiar(texto: str) -> str:
    """Quita de un mensaje cualquier credencial configurada (I4)."""
    texto = str(texto)
    for o in ORIGENES.values():
        for secreto in (o["clave"], o["usuario"]):
            if secreto and len(secreto) >= 3:
                texto = texto.replace(secreto, "***")
    return re.sub(r"(?i)(pwd|password|clave)\s*=\s*[^;\s]+", r"\1=***", texto)


def _driver_odbc(pyodbc):
    preferido = CFG.get("A3_DRIVER")
    if preferido:
        return preferido
    instalados = pyodbc.drivers()
    for d in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
              "ODBC Driver 13 for SQL Server", "SQL Server Native Client 11.0", "SQL Server"):
        if d in instalados:
            return d
    raise ErrorBD("No hay ningún driver ODBC de SQL Server instalado en este equipo. "
                  "Instala 'ODBC Driver 18 for SQL Server' de Microsoft.")


def _conectar(origen: str):
    o = ORIGENES[origen]
    if not (o["usuario"] and o["clave"]) and not o["integrada"]:
        pref = "A3" if origen == "a3" else "INN"
        raise ErrorBD(f"Faltan {pref}_USUARIO y {pref}_CLAVE en conexion_bd.txt para {o['nombre']}.")
    try:
        if origen == "a3":
            import pyodbc
            driver = _driver_odbc(pyodbc)
            servidor = o["servidor"] + (f",{o['puerto']}" if o["puerto"] else "")
            partes = [f"DRIVER={{{driver}}}", f"SERVER={servidor}", f"DATABASE={o['bd']}",
                      "ApplicationIntent=ReadOnly", "APP=Lin-AI (solo lectura)"]
            if o["usuario"] and o["clave"]:
                partes += [f"UID={o['usuario']}", f"PWD={{{o['clave'].replace('}', '}}')}}}"]
            else:
                # Usuario de Windows de este equipo. La comprobación de permisos (I2) bloquea
                # el origen si esa cuenta puede escribir.
                partes.append("Trusted_Connection=yes")
            if driver.startswith("ODBC Driver 18"):
                # El servidor del ERP suele tener certificado propio; se cifra igualmente.
                partes += ["Encrypt=yes", "TrustServerCertificate=yes"]
            con = pyodbc.connect(";".join(partes), autocommit=False, timeout=10)
            con.timeout = TIEMPO_MAX_S  # tiempo máximo por consulta
            cur = con.cursor()
            cur.execute("SET NOCOUNT ON")
            cur.execute("SET LOCK_TIMEOUT 5000")
            # Lectura sin bloquear al ERP (como los informes de A3). Se puede subir a
            # READ COMMITTED en el .env si se prefiere exactitud a no molestar.
            aislamiento = (CFG.get("A3_AISLAMIENTO") or "READ UNCOMMITTED").upper()
            if aislamiento not in ("READ UNCOMMITTED", "READ COMMITTED"):
                aislamiento = "READ UNCOMMITTED"
            cur.execute(f"SET TRANSACTION ISOLATION LEVEL {aislamiento}")
            cur.close()
            return con
        import pymysql
        con = pymysql.connect(
            host=o["servidor"], port=int(o["puerto"] or 3306), user=o["usuario"],
            password=o["clave"], database=o["bd"], charset="utf8mb4", autocommit=False,
            connect_timeout=10, read_timeout=TIEMPO_MAX_S + 5, write_timeout=10)
        with con.cursor() as cur:
            # I3: la propia MariaDB rechazará cualquier escritura en esta sesión.
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(f"SET SESSION max_statement_time = {int(TIEMPO_MAX_S)}")
        return con
    except ErrorBD:
        raise
    except ImportError as e:
        raise ErrorBD(f"Falta una librería de Python ({e.name}). Ejecuta: "
                      "pip install -r bd/requirements_bd.txt")
    except Exception as e:
        texto = _limpiar(e)
        pista = ("Revisa usuario y clave en el .env." if re.search(r"(?i)access denied|login failed|28000|18456", texto)
                 else "¿Está la VPN conectada?")
        raise ErrorBD(f"No se pudo conectar a {o['nombre']} ({o['servidor']}): {texto}. {pista}")


# --------------------------------------------------------------------------- I2: permisos
_PERMISOS_A3 = """
SELECT
  IS_SRVROLEMEMBER('sysadmin') AS sysadmin,
  IS_ROLEMEMBER('db_owner') AS db_owner,
  IS_ROLEMEMBER('db_datawriter') AS db_datawriter,
  IS_ROLEMEMBER('db_ddladmin') AS db_ddladmin,
  HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') AS bd_insert,
  HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') AS bd_update,
  HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE') AS bd_delete,
  HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER') AS bd_alter,
  HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE') AS bd_create_table,
  (SELECT COUNT(*) FROM sys.tables t
    WHERE HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(t.schema_id)) + '.' + QUOTENAME(t.name),
                            'OBJECT', 'INSERT') = 1
       OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(t.schema_id)) + '.' + QUOTENAME(t.name),
                            'OBJECT', 'UPDATE') = 1
       OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(t.schema_id)) + '.' + QUOTENAME(t.name),
                            'OBJECT', 'DELETE') = 1) AS tablas_escribibles
"""
_PRIVILEGIOS_ESCRITURA_MARIADB = re.compile(
    r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|INDEX|EXECUTE|FILE|SUPER|"
    r"GRANT OPTION|TRIGGER|EVENT|REFERENCES|LOCK TABLES|RELOAD|SHUTDOWN|PROCESS|"
    r"CREATE ROUTINE|ALTER ROUTINE|CREATE USER|DELETE HISTORY|BINLOG ADMIN|CONNECTION ADMIN)\b",
    re.IGNORECASE)

_estado = {}  # origen -> {"ok": bool, "motivo": str, "comprobado": iso}


def comprobar_permisos(origen: str, con=None) -> dict:
    """I2: verifica que el usuario no puede escribir. Si puede, el origen queda bloqueado."""
    propia = con is None
    try:
        con = con or _conectar(origen)
        cur = con.cursor()
        if origen == "a3":
            cur.execute(_PERMISOS_A3)
            cols = [c[0] for c in cur.description]
            fila = dict(zip(cols, cur.fetchone()))
            malos = [k for k, v in fila.items() if v]  # None = rol inexistente, cuenta como no
            ok, motivo = (not malos,
                          "solo lectura verificado" if not malos else
                          "el usuario PUEDE escribir (" + ", ".join(malos) + "): origen bloqueado")
        else:
            cur.execute("SHOW GRANTS FOR CURRENT_USER()")
            concesiones = [r[0] for r in cur.fetchall()]
            malos = [g for g in concesiones if _PRIVILEGIOS_ESCRITURA_MARIADB.search(
                g.split(" ON ")[0].replace("GRANT ", "", 1))]
            ok, motivo = (not malos,
                          "solo lectura verificado" if not malos else
                          f"el usuario PUEDE escribir ({len(malos)} permisos de escritura): "
                          "origen bloqueado")
        cur.close()
        con.rollback()
    except ErrorBD as e:
        ok, motivo = False, str(e)
    except Exception as e:
        ok, motivo = False, f"no se pudieron comprobar los permisos: {_limpiar(e)}"
    finally:
        if propia and con is not None:
            try:
                con.close()
            except Exception:
                pass
    _estado[origen] = {"ok": ok, "motivo": motivo, "comprobado": datetime.now().isoformat(timespec="seconds")}
    return _estado[origen]


def estado() -> dict:
    """Situación de cada origen, sin credenciales (sirve para /api/bd/estado)."""
    return {k: {"nombre": o["nombre"], "servidor": o["servidor"], "bd": o["bd"],
                "credenciales_en_env": bool(o["usuario"] and o["clave"]) or o["integrada"],
                **_estado.get(k, {"ok": None, "motivo": "sin comprobar"})}
            for k, o in ORIGENES.items()}


# --------------------------------------------------------------------------- I5: auditoría
def _auditar(**datos):
    datos = {"fecha": datetime.now().isoformat(timespec="seconds"), **datos}
    try:
        with open(AUDITORIA, "a", encoding="utf-8") as f:
            f.write(json.dumps(datos, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- ejecutar
@contextmanager
def _sesion(origen):
    con = _conectar(origen)
    try:
        yield con
    finally:
        try:
            con.rollback()  # I3: nunca se confirma nada
        finally:
            con.close()


def ejecutar(origen: str, sql: str, parametros=(), *, quien: str = "", etiqueta: str = "",
             max_filas: int | None = None) -> dict:
    """Ejecuta una consulta de solo lectura y devuelve {columnas, filas, truncado}.

    Parámetros con marcadores del driver: '?' en A3 (SQL Server), '%s' en Innertia (MariaDB).
    max_filas: para informes se puede subir el límite, nunca por encima de MAX_FILAS_INFORME.
    """
    limite = min(max_filas or MAX_FILAS, MAX_FILAS_INFORME)
    if origen not in ORIGENES:
        raise ErrorBD(f"Origen desconocido: {origen}. Disponibles: {', '.join(ORIGENES)}")
    inicio = time.monotonic()
    registro = {"origen": origen, "quien": quien, "etiqueta": etiqueta, "sql": sql,
                "parametros": list(parametros)}
    try:
        sql = validar_sql(sql)  # I1, antes de tocar la red
        with _sesion(origen) as con:
            if not _estado.get(origen, {}).get("ok"):
                comprobar_permisos(origen, con)  # I2, una vez por arranque
            if not _estado[origen]["ok"]:
                raise ErrorBD(f"{ORIGENES[origen]['nombre']}: {_estado[origen]['motivo']}")
            cur = con.cursor()
            if origen == "innertia":
                cur.execute("START TRANSACTION READ ONLY")
            cur.execute(sql, tuple(parametros))
            if cur.description is None:
                raise ConsultaProhibida("La consulta no devolvió filas (no es un SELECT).")
            columnas = [c[0] for c in cur.description]
            filas = cur.fetchmany(limite + 1)  # I6
            cur.close()
        truncado = len(filas) > limite
        filas = [[_valor(v) for v in f] for f in filas[:limite]]
        _auditar(**registro, filas=len(filas), truncado=truncado,
                 ms=round((time.monotonic() - inicio) * 1000))
        return {"columnas": columnas, "filas": filas, "truncado": truncado}
    except ErrorBD as e:
        _auditar(**registro, error=str(e))
        raise
    except Exception as e:
        msg = _limpiar(e)
        _auditar(**registro, error=msg)
        raise ErrorBD(f"Error en la consulta a {ORIGENES[origen]['nombre']}: {msg}")


def _valor(v):
    """Convierte tipos de la BD (Decimal, fechas, bytes) a algo serializable en JSON."""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v.rstrip() if isinstance(v, str) else v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)
