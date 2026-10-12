"""Lin-AI · generación de Excel y Word con código.

La IA solo redacta el "plan" (título, resumen, secciones y qué columnas mostrar).
Los números y las filas vienen siempre de los datos que pasa el código; la IA nunca
los escribe en el documento.

Plan (dict):
    {"titulo": "Ventas septiembre",
     "resumen": "Texto corto...",
     "secciones": [{"titulo": "Lo más vendido", "texto": "..."}],
     "columnas": ["Cliente", "Importe"]}          # opcional; por defecto, todas
Datos: lista de dicts (una fila por dict), p. ej. el resultado de una consulta.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

try:  # textos.py está junto a app.py; sin él, todo sale en español
    import textos
except ImportError:  # pragma: no cover
    textos = None


def _t(plan, clave, **datos):
    """Texto fijo en el idioma del plan ("idioma": "es"|"zh"|"en") o el de la petición."""
    if textos is None:
        return {"generado": "Generado por Lin-AI el {f}", "informe": "Informe", "hoja_datos": "Datos",
                "hoja_resumen": "Resumen",
                "se_muestran": "Se muestran {m} de {n} filas; el detalle completo va en el Excel."}[clave].format(**datos)
    return textos.t(clave, plan.get("idioma"), **datos)

MAX_FILAS_WORD = 50  # el Word lleva una tabla resumida; el Excel lleva todas las filas
FILAS_A_IA = int(os.environ.get("INFORMES_FILAS_A_IA") or 10)  # filas de muestra que ve la IA


def datos_desde_cursor(cursor):
    """Convierte el resultado de una consulta (pyodbc, sqlite3...) en la lista de dicts que usan los informes."""
    columnas = [c[0] for c in cursor.description]
    return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


def _nombre_archivo(titulo, extension):
    base = re.sub(r"[^\w\-]+", "_", titulo or "informe", flags=re.UNICODE).strip("_")[:60]
    return f"{base or 'informe'}_{datetime.now():%Y%m%d_%H%M%S}.{extension}"


def _columnas(plan, datos):
    todas = list(datos[0].keys()) if datos else []
    pedidas = [c for c in (plan.get("columnas") or []) if c in todas]
    return pedidas or todas


def generar_excel(plan, datos, carpeta):
    """Crea el .xlsx en `carpeta` y devuelve su ruta."""
    columnas = _columnas(plan, datos)
    libro = Workbook()
    hoja = libro.active
    hoja.title = _t(plan, "hoja_datos")
    hoja["A1"] = plan.get("titulo") or _t(plan, "informe")
    hoja["A1"].font = Font(bold=True, size=14)
    hoja["A2"] = _t(plan, "generado", f=f"{datetime.now():%d/%m/%Y %H:%M}")
    hoja["A2"].font = Font(italic=True, color="666666")
    cabecera = 4
    for i, col in enumerate(columnas, 1):
        celda = hoja.cell(row=cabecera, column=i, value=col)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E78")
        celda.alignment = Alignment(horizontal="center")
    for f, fila in enumerate(datos, cabecera + 1):
        for i, col in enumerate(columnas, 1):
            hoja.cell(row=f, column=i, value=fila.get(col))
    for i, col in enumerate(columnas, 1):
        ancho = max([len(str(col))] + [len(str(d.get(col) or "")) for d in datos[:500]])
        hoja.column_dimensions[get_column_letter(i)].width = min(max(ancho + 2, 10), 50)
    if columnas:
        hoja.freeze_panes = hoja.cell(row=cabecera + 1, column=1)
        hoja.auto_filter.ref = f"A{cabecera}:{get_column_letter(len(columnas))}{cabecera + max(len(datos), 1)}"

    if plan.get("resumen") or plan.get("secciones"):
        notas = libro.create_sheet(_t(plan, "hoja_resumen"))
        notas.column_dimensions["A"].width = 100
        filas = [(plan.get("titulo") or _t(plan, "informe"), True), (plan.get("resumen") or "", False)]
        for s in plan.get("secciones") or []:
            filas += [("", False), (s.get("titulo") or "", True), (s.get("texto") or "", False)]
        for n, (texto, negrita) in enumerate(filas, 1):
            notas.cell(row=n, column=1, value=texto).font = Font(bold=negrita)
            notas.cell(row=n, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    ruta = Path(carpeta) / _nombre_archivo(plan.get("titulo"), "xlsx")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    libro.save(ruta)
    return ruta


def generar_word(plan, datos, carpeta):
    """Crea el .docx en `carpeta` y devuelve su ruta."""
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading(plan.get("titulo") or _t(plan, "informe"), level=0)
    fecha = doc.add_paragraph(_t(plan, "generado", f=f"{datetime.now():%d/%m/%Y %H:%M}"))
    fecha.runs[0].italic = True
    if plan.get("resumen"):
        doc.add_heading(_t(plan, "hoja_resumen"), level=1)
        for parrafo in str(plan["resumen"]).split("\n\n"):
            doc.add_paragraph(parrafo.strip())
    for s in plan.get("secciones") or []:
        doc.add_heading(s.get("titulo") or "", level=1)
        for parrafo in str(s.get("texto") or "").split("\n\n"):
            doc.add_paragraph(parrafo.strip())

    columnas = _columnas(plan, datos)
    if columnas and datos:
        doc.add_heading(_t(plan, "hoja_datos"), level=1)
        tabla = doc.add_table(rows=1, cols=len(columnas))
        tabla.style = "Light Grid Accent 1"
        for i, col in enumerate(columnas):
            tabla.rows[0].cells[i].text = str(col)
        for fila in datos[:MAX_FILAS_WORD]:
            celdas = tabla.add_row().cells
            for i, col in enumerate(columnas):
                valor = fila.get(col)
                celdas[i].text = "" if valor is None else str(valor)
        if len(datos) > MAX_FILAS_WORD:
            doc.add_paragraph(_t(plan, "se_muestran", m=MAX_FILAS_WORD, n=len(datos))).runs[0].italic = True

    ruta = Path(carpeta) / _nombre_archivo(plan.get("titulo"), "docx")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc.save(ruta)
    return ruta


def resumen_datos(datos):
    """Lo que ve la IA: estructura y totales calculados aquí, no la tabla entera."""
    columnas = list(datos[0].keys()) if datos else []
    totales = {}
    for c in columnas:
        numeros = [v for v in (d.get(c) for d in datos) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeros and len(numeros) >= len(datos) * 0.8:
            totales[c] = {"suma": round(sum(numeros), 2), "minimo": min(numeros), "maximo": max(numeros),
                          "media": round(sum(numeros) / len(numeros), 2)}
    return {"filas": len(datos), "columnas": columnas, "totales_numericos": totales,
            "muestra": datos[:FILAS_A_IA]}


def _texto(v, limite):
    return str(v or "").strip()[:limite]


def limpiar_plan(bruto, datos):
    """Acepta solo las claves esperadas y con tamaño razonable."""
    try:
        plan = json.loads(bruto)
    except (TypeError, json.JSONDecodeError):
        m = re.search(r"\{.*\}", bruto or "", re.S)
        plan = json.loads(m.group(0)) if m else {}
    columnas = list(datos[0].keys()) if datos else []
    return {
        "titulo": _texto(plan.get("titulo"), 120) or "Informe",
        "resumen": _texto(plan.get("resumen"), 4000),
        "secciones": [{"titulo": _texto(s.get("titulo"), 120), "texto": _texto(s.get("texto"), 4000)}
                      for s in (plan.get("secciones") or [])[:4] if isinstance(s, dict)],
        "columnas": [c for c in (plan.get("columnas") or []) if c in columnas],
        "asunto_correo": _texto(plan.get("asunto_correo"), 200),
        "cuerpo_correo": _texto(plan.get("cuerpo_correo"), 600),
    }
