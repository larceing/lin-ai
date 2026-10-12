"""Lin-AI · la IA como orquestador.

La IA entiende la petición, da contexto y decide qué herramienta programada usar y con
qué parámetros. Las herramientas son código:
- consultar: ejecuta una consulta PREDEFINIDA y registrada (la IA no escribe SQL).
- crear_excel / crear_word: generan el archivo con las filas reales de una consulta.
- preparar_correo: solo si CORREO_ACTIVO=si; deja un borrador que una persona confirma.
La IA solo ve un resumen de los datos (columnas, nº de filas, totales y unas filas de muestra).

Registrar una consulta (lo hará el hilo de la base de datos):
    from informes.orquestador import registrar_consulta
    registrar_consulta("ventas_mes", "Ventas por cliente de un mes",
                       {"anio": "integer", "mes": "integer"}, funcion_que_devuelve_filas)
"""
import json
import os
from pathlib import Path

from . import correo
from .documentos import generar_excel, generar_word, limpiar_plan, resumen_datos

MAX_RONDAS = 6
MAX_CORREOS = 1  # borradores de correo por petición
CONSULTAS = {}

SISTEMA = (
    "Eres Lin-AI, asistente interno de SPSIL. Eres el orquestador: entiendes lo que piden, das "
    "contexto y usas las herramientas para obtener datos y crear archivos. Nunca inventes cifras: "
    "solo usa las que devuelven las herramientas. Para crear un Excel o Word, primero obtén los "
    "datos con 'consultar' (o usa 'datos_usuario' si te los han pasado). Si ninguna consulta sirve, "
    "dilo. Responde en español claro de negocio, sin SQL ni nombres técnicos. "
    "Correo: NO lo redactes. El programa monta el correo con su plantilla y resume él mismo los "
    "adjuntos (filas, columnas, totales). Tú solo das destinatarios, archivos, un título corto y, "
    "si aporta algo, una nota de una o dos frases sin cifras, enlaces ni direcciones. Solo se "
    "prepara un borrador por petición y nunca se envía sin que una persona lo confirme."
)


def registrar_consulta(nombre, descripcion, parametros, funcion):
    """parametros: {"nombre": "integer"|"string"|"number"}; funcion(**params) -> lista de dicts."""
    CONSULTAS[nombre] = {"descripcion": descripcion, "parametros": parametros, "funcion": funcion}


def correo_activo():
    return (os.environ.get("CORREO_ACTIVO") or "").strip().lower() in ("si", "sí", "1", "true")


def _herramienta(nombre, descripcion, propiedades, requeridos):
    return {"type": "function", "function": {"name": nombre, "description": descripcion, "parameters": {
        "type": "object", "properties": propiedades, "required": requeridos}}}


def herramientas():
    texto = {"type": "string"}
    documento = {
        "datos_id": {"type": "string", "description": "Id devuelto por 'consultar' o 'datos_usuario'"},
        "titulo": texto, "resumen": texto,
        "secciones": {"type": "array", "items": {"type": "object", "properties": {
            "titulo": texto, "texto": texto}}},
        "columnas": {"type": "array", "items": texto, "description": "Columnas a mostrar, en orden"},
    }
    lista = []
    if CONSULTAS:
        catalogo = "; ".join(f"{n}: {c['descripcion']} (parámetros: {', '.join(c['parametros']) or 'ninguno'})"
                             for n, c in CONSULTAS.items())
        lista.append(_herramienta(
            "consultar", f"Ejecuta una consulta de solo lectura del ERP. Disponibles: {catalogo}",
            {"consulta": {"type": "string", "enum": list(CONSULTAS)},
             "parametros": {"type": "object", "description": "Valores de los parámetros de la consulta"}},
            ["consulta"]))
    lista.append(_herramienta("crear_excel", "Crea un Excel con todas las filas de unos datos.", documento,
                              ["datos_id", "titulo"]))
    lista.append(_herramienta("crear_word", "Crea un informe Word con texto y una tabla de unos datos.",
                              documento, ["datos_id", "titulo"]))
    if correo_activo():
        lista.append(_herramienta(
            "preparar_correo", "Prepara un borrador de correo con la plantilla de SPSIL y archivos "
            "creados en esta petición. NO lo envía: una persona lo revisa y confirma.",
            {"para": {"type": "array", "items": texto},
             "archivos": {"type": "array", "items": texto, "description": "Nombres devueltos por crear_excel/crear_word"},
             "asunto": {"type": "string", "description": "Solo el título, máx. 90 caracteres (el programa añade prefijo y fecha)"},
             "cuerpo": {"type": "string", "description": "Nota opcional, 1-2 frases sin cifras. Vacío si no aporta nada"}},
            ["para", "archivos"]))
    return lista


class Sesion:
    """Una petición al asistente: guarda los datos y archivos que van saliendo."""

    def __init__(self, carpeta, autor="anónimo"):
        self.salida = Path(carpeta) / "informes_generados"
        self.ruta_bd = Path(carpeta) / "correo.db"
        self.autor = autor
        self.datos, self.archivos, self.borradores, self.pasos = {}, [], [], []

    def guardar_datos(self, filas, origen):
        ident = f"datos_{len(self.datos) + 1}"
        self.datos[ident] = filas
        return {"datos_id": ident, "origen": origen, **resumen_datos(filas)}

    def ejecutar(self, nombre, args):
        if nombre == "consultar":
            c = CONSULTAS.get(args.get("consulta"))
            if not c:
                return {"error": "Esa consulta no existe."}
            params = {k: v for k, v in (args.get("parametros") or {}).items() if k in c["parametros"]}
            return self.guardar_datos(c["funcion"](**params), args["consulta"])
        if nombre in ("crear_excel", "crear_word"):
            filas = self.datos.get(args.get("datos_id"))
            if filas is None:
                return {"error": "Ese datos_id no existe; primero usa 'consultar'."}
            plan = limpiar_plan(json.dumps(args), filas)
            ruta = (generar_excel if nombre == "crear_excel" else generar_word)(plan, filas, self.salida)
            self.archivos.append(ruta.name)
            return {"archivo": ruta.name, "filas": len(filas)}
        if nombre == "preparar_correo" and correo_activo():
            if len(self.borradores) >= MAX_CORREOS:
                return {"error": "Ya hay un borrador preparado en esta petición."}
            pedidos = [Path(a).name for a in args.get("archivos") or []]
            if any(a not in self.archivos for a in pedidos):
                return {"error": "Solo se pueden adjuntar archivos creados en esta petición."}
            try:
                b = correo.preparar(self.ruta_bd, args.get("para") or [], args.get("asunto"),
                                    args.get("cuerpo"), [self.salida / a for a in pedidos], self.autor)
            except correo.ErrorCorreo as e:
                return {"error": str(e)}
            self.borradores.append(b)
            return {"borrador_id": b["id"], "asunto": b["asunto"], "estado": "pendiente de confirmar por una persona"}
        return {"error": f"Herramienta no disponible: {nombre}"}


def atender(completar, carpeta, mensaje, datos_usuario=None, autor="anónimo"):
    """`completar(mensajes, herramientas)` llama a la IA y devuelve su mensaje (lo pone app.py)."""
    s = Sesion(carpeta, autor)
    contexto = ""
    if datos_usuario:
        r = s.guardar_datos(datos_usuario, "datos_usuario")
        contexto = "\n\nDatos que te pasan: " + json.dumps(r, ensure_ascii=False, default=str)
    mensajes = [{"role": "system", "content": SISTEMA},
                {"role": "user", "content": mensaje + contexto}]
    texto = "No he podido terminar la petición en los pasos permitidos."
    for _ in range(MAX_RONDAS):
        m = completar(mensajes, herramientas())
        llamadas = getattr(m, "tool_calls", None) or []
        if not llamadas:
            texto = m.content or ""
            break
        mensajes.append({"role": "assistant", "content": m.content or "", "tool_calls": [
            {"id": t.id, "type": "function",
             "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in llamadas]})
        for t in llamadas:
            try:
                args = json.loads(t.function.arguments or "{}")
                resultado = s.ejecutar(t.function.name, args)
            except Exception as e:  # un fallo de una herramienta se le cuenta a la IA, no tumba la app
                resultado = {"error": f"{type(e).__name__}: {e}"}
            s.pasos.append({"herramienta": t.function.name, "resultado":
                            {k: v for k, v in resultado.items() if k != "muestra"}})
            mensajes.append({"role": "tool", "tool_call_id": t.id,
                             "content": json.dumps(resultado, ensure_ascii=False, default=str)})
    return {"respuesta": texto, "archivos": s.archivos, "borradores_correo": s.borradores, "pasos": s.pasos}
