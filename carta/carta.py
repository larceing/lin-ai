"""Lin-AI · la carta: la IA atiende, el código cocina.

  1. La persona pide en sus palabras ("quiero las ventas de la última semana").
  2. La IA (el camarero) interpreta: qué datos, qué artículos, qué fechas reales y en qué formato.
     Si algo cambia el resultado y no está claro, pregunta. Después PROPONE un plato de la carta
     con sus datos concretos. No ejecuta nada.
  3. La persona confirma (y puede cambiar el formato) o dice "no es esto".
  4. El código (los cocineros) ejecuta la consulta aprobada en solo lectura y crea la tabla,
     el Excel o el Word. La IA no escribe ninguna cifra.
  5. Cada pedido confirmado o rechazado se guarda y se le enseña a la IA la próxima vez, para
     que acierte antes ("la última semana" = lunes a domingo anteriores, por ejemplo).
"""
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import textos
from bd import consultas, lectura

FORMATOS = {"pantalla": "tabla en pantalla", "excel": "Excel (.xlsx)", "word": "informe Word (.docx)"}
FILAS_PANTALLA = 200
CADUCIDAD_MIN = 60

# Platos: lo que se puede pedir. Cada uno es una consulta del catálogo cerrado (bd/consultas.py).
PLATOS = {
    "ranking_comerciales": {
        "consulta": "a3_ranking_comerciales",
        "titulo": "Ranking de comerciales",
        "para_la_ia": "Los N mejores comerciales por lo facturado (importe, o unidades si lo piden).",
    },
    "ventas_por_familia": {
        "consulta": "a3_ventas_familia",
        "titulo": "Ventas por familia",
        "para_la_ia": "Lo facturado por familia de artículo (importe, o unidades si lo piden).",
    },
    "ventas_por_articulo": {
        "consulta": "a3_ventas_periodo",
        "titulo": "Ventas por artículo",
        "para_la_ia": "Pedidos de venta de un periodo, por artículo (unidades y nº de pedidos). "
                      "Es la base de ventas que usa la empresa desde el 6/10/2026.",
    },
    "ventas_por_dia": {
        "consulta": "a3_ventas_por_dia",
        "titulo": "Ventas día a día",
        "para_la_ia": "Pedidos de venta de un periodo, sumados por día (evolución).",
    },
    "facturado_por_articulo": {
        "consulta": "a3_facturado_periodo",
        "titulo": "Unidades facturadas por artículo",
        "para_la_ia": "Facturas de venta de un periodo, por artículo. Úsalo solo si piden "
                      "facturas o facturación; 'ventas' a secas son pedidos de venta.",
    },
    "stock_por_almacen": {
        "consulta": "a3_stock_articulo",
        "titulo": "Stock por almacén",
        "para_la_ia": "Stock actual de uno o varios artículos en cada almacén de A3. Almacenes: "
                      "1 = Almacén 1, 14 = Almacén 2 (ASPE), 16 = Toledo.",
    },
    "pendiente_de_recibir": {
        "consulta": "a3_pedidos_proveedor_pendientes",
        "titulo": "Pedidos a proveedor pendientes de recibir",
        "para_la_ia": "Unidades pedidas a proveedores que aún no han llegado (reservas).",
    },
    "ficha_stock_articulo": {
        "consulta": "a3_ofertas_stock_articulo",
        "titulo": "Ficha de stock del artículo",
        "para_la_ia": "Todo el cálculo del informe de stock para UN artículo (código exacto): "
                      "stock, reservas, ventas recientes, mínimo, punto de pedido y máximo.",
    },
    "buscar_articulo": {
        "consulta": "a3_buscar_articulo",
        "titulo": "Búsqueda de artículos",
        "para_la_ia": "Lista de artículos cuyo código o descripción contiene un texto.",
    },
}

class AvisoPedido(lectura.ErrorBD):
    """Aviso para la persona (ya traducido): pedido inexistente, caducado, repetido..."""


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


# --------------------------------------------------------------------------- almacenamiento
def preparar_bd(ruta_bd):
    with closing(sqlite3.connect(ruta_bd)) as con, con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id TEXT PRIMARY KEY, conversacion TEXT, frase TEXT, plato TEXT,
                parametros TEXT, formato TEXT, interpretacion TEXT, estado TEXT,
                motivo TEXT, creado TEXT, servido TEXT, autor TEXT, resultado TEXT);
            CREATE TABLE IF NOT EXISTS pedido_mensajes (
                id INTEGER PRIMARY KEY, conversacion TEXT, rol TEXT, contenido TEXT, fecha TEXT);
        """)
        columnas = [c[1] for c in con.execute("PRAGMA table_info(pedidos)")]
        for col in ("canal", "para"):
            if col not in columnas:
                con.execute(f"ALTER TABLE pedidos ADD COLUMN {col} TEXT")


# --------------------------------------------------------------------------- canal de entrega
# El correo es un canal, no una herramienta: cualquier respuesta (datos o explicación) puede ir
# por correo. Siempre como borrador que una persona confirma (POST /api/correo/{id}/confirmar),
# solo a destinatarios permitidos y solo con CORREO_ACTIVO=si.
CANALES = ("pantalla", "correo")


def _lista_para(valor):
    if not valor:
        return []
    if isinstance(valor, str):
        valor = re.split(r"[,;\s]+", valor)
    return [str(v).strip() for v in valor if str(v).strip()][:5]


def preparar_envio(carpeta, archivos, titulo, para, autor, idioma):
    """Deja un borrador de correo con los archivos. Devuelve (borrador, aviso): uno de los dos."""
    from informes import correo
    from informes.orquestador import correo_activo

    if not correo_activo():
        return None, textos.t("correo_apagado", idioma)
    cfg = correo.config()
    destino = _lista_para(para) or [d for d in cfg["permitidos"] if not d.startswith("@")][:1]
    if not destino:
        return None, textos.t("correo_sin_destino", idioma)
    salida = Path(carpeta) / "informes_generados"
    try:
        b = correo.preparar(Path(carpeta) / "correo.db", destino, titulo, "",
                            [salida / a for a in archivos], autor)
    except correo.ErrorCorreo as e:
        print(f"Correo no preparado: {e}")
        detalle = f" ({e})" if idioma == "es" else ""
        return None, textos.t("correo_no_preparado", idioma) + detalle
    b["confirmar"] = f"/api/correo/{b['id']}/confirmar"
    return b, None


def _con(ruta_bd):
    con = sqlite3.connect(ruta_bd)
    con.row_factory = sqlite3.Row
    return con


def _ahora():
    return datetime.now().isoformat(timespec="seconds")


def aprendizajes(ruta_bd, limite=8):
    """Pedidos ya confirmados (y los rechazados con su motivo), del más reciente al más antiguo."""
    with closing(_con(ruta_bd)) as con:
        return [dict(f) for f in con.execute(
            "SELECT frase, plato, parametros, formato, interpretacion, estado, motivo, creado "
            "FROM pedidos WHERE estado IN ('servido', 'rechazado') ORDER BY creado DESC LIMIT ?",
            (limite,))]


# --------------------------------------------------------------------------- lo que ve la IA
def instrucciones(ruta_bd, fichas="", hoy: date | None = None):
    """Mensaje de sistema. Lo fijo va primero y lo que cambia (fecha, pedidos anteriores,
    ficha) al final, para que OpenAI pueda reutilizar el principio desde su caché."""
    hoy = hoy or date.today()
    carta = []
    for n, p in PLATOS.items():
        c = consultas.CATALOGO[p["consulta"]]
        datos = ", ".join(k + ("?" if k in c.get("opcionales", []) else "") for k in c["parametros"])
        carta.append(f"- {n} ({datos or 'sin datos'}): {p['para_la_ia']}")
    previos = []
    for a in aprendizajes(ruta_bd, 8):
        estado = "OK" if a["estado"] == "servido" else f"NO: {a['motivo'] or 'sin motivo'}"
        previos.append(f"- «{a['frase'][:120]}» → {a['plato']} {a['parametros']} {a['formato']} [{estado}]")
    return f"""Eres Lin-AI, asistente interno de SPSIL (A3ERP). Eres el camarero: entiendes lo que
piden, preguntas lo que falte y propones un plato de la carta. No ejecutas nada ni das cifras:
el programa las calcula cuando la persona confirma. Usa SIEMPRE una herramienta:
- proponer: plato, datos y formato. Pasa "la última semana", "este mes", "septiembre"... a
  fechas AAAA-MM-DD y explícalo en 'interpretacion' (qué datos, qué artículos, qué fechas).
- preguntar: solo si la duda cambia el resultado y los pedidos anteriores no la resuelven.
  Da 3 opciones concretas, cada una con medida y fechas (p. ej. "Este año hasta hoy, por
  importe facturado"). No preguntes el formato con esta herramienta.
- Si piden el formato (Excel, Word, aquí...), úsalo. Si no lo dicen, formato "preguntar": el
  programa le ofrecerá Excel, Word o pantalla. "Los N mejores" = top N; sin periodo en un
  ranking, usa el año en curso hasta hoy y dilo en 'interpretacion' (no preguntes).
- responder: dudas de cálculo (con la ficha) o si ningún plato sirve (sugiere el más
  parecido). PowerPoint aún no está.
- Canal: si piden recibirlo por correo ("envíamelo", "mándalo a..."), canal "correo" (y
  'para' si dan una dirección); si no, "pantalla". Vale para proponer y para responder.
Datos con ? son opcionales (vacío = todos). Español de negocio, sin SQL.

CARTA:
{chr(10).join(carta)}

Hoy es {DIAS[hoy.weekday()]} {hoy.isoformat()}.

PEDIDOS ANTERIORES (OK = confirmado: interpreta igual; NO = rechazado: no lo repitas):
{chr(10).join(previos) or '- Todavía no hay.'}

{fichas}"""


def herramientas():
    texto = {"type": "string"}
    canal = {"type": "string", "enum": list(CANALES)}
    para = {"type": "array", "items": texto, "description": "Direcciones, solo si las dicen"}
    return [
        {"type": "function", "function": {
            "name": "proponer", "description": "Propone un plato de la carta con sus datos. No lo ejecuta.",
            "parameters": {"type": "object", "properties": {
                "plato": {"type": "string", "enum": list(PLATOS)},
                "parametros": {"type": "object", "description": "Datos del plato (fechas AAAA-MM-DD)"},
                "formato": {"type": "string", "enum": [*FORMATOS, PREGUNTAR]},
                "interpretacion": {"type": "string", "description": "Qué has entendido, con fechas exactas"},
                "canal": canal, "para": para,
            }, "required": ["plato", "parametros", "formato", "interpretacion"]}}},
        {"type": "function", "function": {
            "name": "preguntar", "description": "Pregunta lo que falte, con opciones.",
            "parameters": {"type": "object", "properties": {
                "texto": texto, "opciones": {"type": "array", "items": texto, "maxItems": 4}},
                "required": ["texto"]}}},
        {"type": "function", "function": {
            "name": "responder", "description": "Responde sin datos del ERP (explicaciones, o si ningún plato sirve).",
            "parameters": {"type": "object", "properties": {
                "texto": texto, "canal": canal, "para": para,
                "titulo": {"type": "string", "description": "Título corto si va por correo"}},
                "required": ["texto"]}}},
    ]


PREGUNTAR = "preguntar"  # formato sin decidir: el programa ofrece Excel, Word o pantalla


def validar_propuesta(args):
    """Comprueba lo que propone la IA. Devuelve el pedido limpio o lanza ErrorBD."""
    plato = args.get("plato")
    if plato not in PLATOS:
        raise lectura.ErrorBD(f"El plato «{plato}» no está en la carta.")
    formato = args.get("formato") if args.get("formato") in (*FORMATOS, PREGUNTAR) else PREGUNTAR
    parametros = consultas.normalizar(PLATOS[plato]["consulta"], args.get("parametros") or {})
    interpretacion = str(args.get("interpretacion") or "").strip()[:500]
    canal = args.get("canal") if args.get("canal") in CANALES else "pantalla"
    return {"plato": plato, "parametros": parametros, "formato": formato,
            "interpretacion": interpretacion, "canal": canal, "para": _lista_para(args.get("para"))}


def describir(pedido, idioma="es", con_formato=True):
    """Frase que ve la persona, escrita por el código a partir de la propuesta."""
    p = pedido["parametros"]
    partes = [textos.plato(pedido["plato"], idioma)]
    if p.get("desde"):
        d, h = date.fromisoformat(p["desde"]), date.fromisoformat(p["hasta"])
        partes.append(textos.t("periodo", idioma, d=textos.fecha(d, idioma), h=textos.fecha(h, idioma)))
    if "articulo" in p:
        partes.append(textos.t("articulo", idioma, a=p["articulo"]) if p["articulo"]
                      else textos.t("todos_articulos", idioma))
    if p.get("top"):
        partes.append(textos.t("top", idioma, n=p["top"]))
    if con_formato and pedido["formato"] in FORMATOS:
        partes.append(textos.formato(pedido["formato"], idioma))
    return " · ".join(partes)


# --------------------------------------------------------------------------- atender
def atender(ruta_bd, completar, conversacion, mensaje, autor="anónimo", fichas="", idioma="es",
            carpeta=None):
    """La IA interpreta el mensaje. Devuelve una pregunta, una respuesta o una propuesta.

    `completar(mensajes, herramientas)` lo pone app.py (modelos aprobados y tope de gasto) y
    devuelve el mensaje de la IA, que debe traer una llamada a herramienta.
    """
    with closing(_con(ruta_bd)) as con:
        previos = con.execute(
            "SELECT rol, contenido FROM pedido_mensajes WHERE conversacion=? ORDER BY id DESC LIMIT 6",
            (conversacion,)).fetchall()[::-1]
    mensajes = [{"role": "system",
                 "content": instrucciones(ruta_bd, fichas) + textos.instruccion_ia(idioma)}]
    mensajes += [{"role": m["rol"], "content": m["contenido"]} for m in previos]
    mensajes.append({"role": "user", "content": mensaje})
    frase = _frase_original(previos, mensaje)

    salida = None
    for _ in range(2):  # si la propuesta no es válida, se le explica a la IA una vez
        m = completar(mensajes, herramientas())
        llamadas = getattr(m, "tool_calls", None) or []
        if not llamadas:
            salida = {"tipo": "respuesta", "texto": m.content or ""}
            break
        t = llamadas[0]
        try:
            args = json.loads(t.function.arguments or "{}")
        except ValueError:
            args = {}
        if t.function.name == "preguntar":
            salida = {"tipo": "pregunta", "texto": str(args.get("texto", ""))[:1000],
                      "opciones": [str(o)[:120] for o in (args.get("opciones") or [])][:4]}
            break
        if t.function.name == "responder":
            salida = {"tipo": "respuesta", "texto": str(args.get("texto", ""))[:4000], "canal": "pantalla"}
            if args.get("canal") == "correo" and carpeta and salida["texto"]:
                salida.update(_explicacion_por_correo(carpeta, salida["texto"], args, autor, idioma))
            break
        try:
            pedido = validar_propuesta(args)
        except lectura.ErrorBD as e:
            mensajes.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": t.id, "type": "function",
                 "function": {"name": t.function.name, "arguments": t.function.arguments}}]})
            mensajes.append({"role": "tool", "tool_call_id": t.id,
                             "content": f"Propuesta no válida: {e} Corrígela o pregunta."})
            continue
        pedido.update(id=uuid.uuid4().hex[:12], texto=describir(pedido, idioma),
                      titulo=textos.plato(pedido["plato"], idioma), tipo="propuesta")
        if pedido["formato"] == PREGUNTAR:
            # La pregunta del formato la hace el programa: sin otra llamada a la IA.
            pedido["pregunta_formato"] = textos.t("que_formato", idioma)
            pedido["opciones_formato"] = {f: textos.formato_corto(f, idioma) for f in ("excel", "word", "pantalla")}
        with closing(_con(ruta_bd)) as con, con:
            con.execute(
                "INSERT INTO pedidos (id, conversacion, frase, plato, parametros, formato, "
                "interpretacion, estado, creado, autor, canal, para) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (pedido["id"], conversacion, frase, pedido["plato"],
                 json.dumps(pedido["parametros"], ensure_ascii=False), pedido["formato"],
                 pedido["interpretacion"], "pendiente", _ahora(), autor, pedido["canal"],
                 ",".join(pedido["para"])))
        salida = pedido
        break
    if salida is None:
        salida = {"tipo": "respuesta", "texto": textos.t("no_encaja", idioma)}

    visible = salida.get("texto", "")
    if salida["tipo"] == "propuesta":
        visible = f"Propuesta: {salida['texto']}. {salida['interpretacion']}"
    with closing(_con(ruta_bd)) as con, con:
        con.executemany(
            "INSERT INTO pedido_mensajes (conversacion, rol, contenido, fecha) VALUES (?,?,?,?)",
            [(conversacion, "user", mensaje, _ahora()), (conversacion, "assistant", visible, _ahora())])
    return salida


def _explicacion_por_correo(carpeta, texto, args, autor, idioma):
    """Una explicación (sin datos del ERP) por correo: va en un Word adjunto, porque el cuerpo
    del correo lo monta la plantilla y no admite cifras que no salgan de un adjunto."""
    from informes.documentos import generar_word

    titulo = str(args.get("titulo") or "").strip()[:90] or textos.t("explicacion", idioma)
    ruta = generar_word({"titulo": titulo, "resumen": texto, "secciones": [], "columnas": [],
                         "idioma": idioma}, [], Path(carpeta) / "informes_generados")
    borrador, aviso = preparar_envio(carpeta, [ruta.name], titulo, args.get("para"), autor, idioma)
    return {"canal": "correo", "archivos": [ruta.name], "borrador_correo": borrador, "aviso_correo": aviso}


def _frase_original(previos, mensaje):
    """La petición que se aprende: los mensajes de la persona desde la última propuesta."""
    frases = []
    for m in reversed(previos):
        if m["rol"] == "assistant" and m["contenido"].startswith("Propuesta:"):
            break
        if m["rol"] == "user":
            frases.insert(0, m["contenido"])
    return " / ".join(frases + [mensaje])[:500]


# --------------------------------------------------------------------------- cocinar
def _pedido(ruta_bd, ident, idioma):
    with closing(_con(ruta_bd)) as con:
        f = con.execute("SELECT * FROM pedidos WHERE id=?", (ident,)).fetchone()
    if not f:
        raise AvisoPedido(textos.t("pedido_no_existe", idioma))
    if f["estado"] != "pendiente":
        raise AvisoPedido(textos.t("pedido_servido" if f["estado"] == "servido" else "pedido_rechazado", idioma))
    return dict(f)


def cocinar(ruta_bd, carpeta, ident, formato=None, quien="anónimo", idioma="es", canal=None, para=None):
    """Ejecuta un pedido confirmado: consulta de solo lectura + tabla, Excel o Word."""
    from informes.documentos import generar_excel, generar_word, resumen_datos

    p = _pedido(ruta_bd, ident, idioma)
    if (datetime.now() - datetime.fromisoformat(p["creado"])).total_seconds() > CADUCIDAD_MIN * 60:
        raise AvisoPedido(textos.t("caducado", idioma))
    formato = formato if formato in FORMATOS else p["formato"] if p["formato"] in FORMATOS else "pantalla"
    canal = canal if canal in CANALES else (p.get("canal") or "pantalla")
    para = _lista_para(para) or _lista_para(p.get("para"))
    parametros = json.loads(p["parametros"])
    plato = PLATOS[p["plato"]]
    r = consultas.ejecutar(plato["consulta"], parametros, quien=quien,
                           max_filas=None if formato == "pantalla" else lectura.MAX_FILAS_INFORME)
    columnas = [textos.columna(c, idioma) for c in r["columnas"]]
    filas = [dict(zip(columnas, f)) for f in r["filas"]]
    pedido = {"plato": p["plato"], "parametros": parametros, "formato": formato}
    titulo = describir(pedido, idioma, con_formato=False)
    res = resumen_datos([dict(zip(r["columnas"], f)) for f in r["filas"]])
    totales = ", ".join(f"{textos.columna(c, idioma)}: {v['suma']:,.0f}".replace(",", ".")
                        for c, v in res["totales_numericos"].items()
                        if c in ("importe", "unidades", "pedidos", "facturas"))
    resumen = (f"{p['interpretacion']}\n\n{textos.t('filas', idioma, n=len(filas))}"
               + (f" · {textos.t('totales', idioma)}: {totales}" if totales else "")
               + (" · " + textos.t("recortado", idioma) if r["truncado"] else ""))
    archivos = []
    if formato in ("excel", "word") or canal == "correo":  # por correo siempre va un archivo
        plan = {"titulo": titulo, "resumen": resumen, "secciones": [], "columnas": columnas,
                "idioma": idioma}
        salida = Path(carpeta) / "informes_generados"
        ruta = (generar_word if formato == "word" else generar_excel)(plan, filas, salida)
        archivos.append(ruta.name)
    resultado = {"titulo": titulo, "resumen": resumen, "columnas": columnas,
                 "filas": r["filas"][:FILAS_PANTALLA], "total_filas": len(filas),
                 "truncado": r["truncado"], "archivos": archivos, "formato": formato, "canal": canal}
    if canal == "correo":
        resultado["borrador_correo"], resultado["aviso_correo"] = preparar_envio(
            carpeta, archivos, titulo, para, quien, idioma)
    with closing(_con(ruta_bd)) as con, con:
        con.execute("UPDATE pedidos SET estado='servido', servido=?, formato=?, resultado=? WHERE id=?",
                    (_ahora(), formato, json.dumps({"filas": len(filas), "archivos": archivos}), ident))
    return resultado


def rechazar(ruta_bd, ident, motivo="", idioma="es"):
    _pedido(ruta_bd, ident, idioma)
    with closing(_con(ruta_bd)) as con, con:
        con.execute("UPDATE pedidos SET estado='rechazado', motivo=? WHERE id=?",
                    (str(motivo or "").strip()[:300], ident))
    return {"ok": True}
