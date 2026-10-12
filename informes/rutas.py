"""Lin-AI · rutas web para informes (Excel/Word) y correo.

Reparto del trabajo:
- La IA recibe la petición y un resumen de los datos (columnas, nº de filas, totales
  calculados por el código y unas pocas filas de muestra) y devuelve un plan en JSON:
  título, resumen, secciones y columnas a mostrar.
- El código genera el Excel y el Word con los datos reales y, si se pide, los envía por
  correo (con borrador y confirmación, ver correo.py).

Integración en app.py (dos líneas, ver README.md):
    from informes.rutas import crear_router
    app.include_router(crear_router(AQUI, llamar_ia))
"""
import json
import os
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import correo
from .documentos import generar_excel, generar_word, limpiar_plan, resumen_datos
from .orquestador import atender

MAX_FILAS = 50_000

INSTRUCCIONES = (
    "Eres Lin-AI, asistente interno de SPSIL. Vas a preparar el texto de un informe que el "
    "programa convertirá en Excel y Word. NO escribas cifras que no estén en el resumen de datos "
    "que te paso; las tablas las pone el programa. Responde SOLO con un objeto JSON con esta forma: "
    '{"titulo": str, "resumen": str, "secciones": [{"titulo": str, "texto": str}], '
    '"columnas": [nombres de columnas a mostrar, en orden], "asunto_correo": str, "cuerpo_correo": str}. '
    "asunto_correo es solo un título corto; cuerpo_correo, una o dos frases sin cifras (el programa "
    "pone el saludo y el resumen de los adjuntos). Español claro de negocio, sin SQL ni nombres "
    "técnicos. Máximo 4 secciones."
)


class PeticionInforme(BaseModel):
    peticion: str = ""
    datos: list[dict] = Field(default_factory=list)
    formatos: list[str] = Field(default_factory=lambda: ["xlsx", "docx"])
    plan: dict | None = None  # si viene, no se llama a la IA


class PeticionAsistente(BaseModel):
    mensaje: str
    datos: list[dict] = Field(default_factory=list)  # opcional, mientras no hay consultas registradas
    autor: str = "anónimo"


class PeticionCorreo(BaseModel):
    para: list[str]
    asunto: str = ""   # solo el título; la plantilla añade "[Lin-AI SPSIL]" y la fecha
    cuerpo: str = ""   # nota breve opcional; el resumen de los adjuntos lo pone la plantilla
    archivos: list[str] = Field(default_factory=list)  # nombres devueltos por /api/informes
    autor: str = "anónimo"


def crear_router(carpeta: Path, llamar_ia: Callable[[list], str] | None = None,
                 completar: Callable[[list, list], object] | None = None):
    """Las dos funciones de IA las pone app.py (con su tope de gasto y modelos aprobados):
    `llamar_ia(mensajes) -> texto` y `completar(mensajes, herramientas) -> mensaje de la IA`."""
    salida = Path(carpeta) / "informes_generados"
    ruta_bd = Path(carpeta) / "correo.db"
    router = APIRouter()

    def archivo_seguro(nombre):
        ruta = (salida / Path(nombre).name).resolve()
        if ruta.parent != salida.resolve() or not ruta.is_file():
            raise HTTPException(404, f"No existe el archivo {nombre}")
        return ruta

    @router.post("/api/informes")
    def crear_informe(p: PeticionInforme):
        if len(p.datos) > MAX_FILAS:
            raise HTTPException(400, f"Demasiadas filas ({len(p.datos)}); máximo {MAX_FILAS}.")
        formatos = [f for f in p.formatos if f in ("xlsx", "docx")]
        if not formatos:
            raise HTTPException(400, "Formatos válidos: xlsx, docx")
        if p.plan is not None:
            plan = limpiar_plan(json.dumps(p.plan), p.datos)
        else:
            if llamar_ia is None:
                raise HTTPException(503, "La IA no está disponible; manda un plan propio.")
            mensajes = [{"role": "system", "content": INSTRUCCIONES},
                        {"role": "user", "content": f"Petición: {p.peticion}\n\nResumen de datos:\n"
                                                    + json.dumps(resumen_datos(p.datos), ensure_ascii=False, default=str)}]
            try:
                plan = limpiar_plan(llamar_ia(mensajes), p.datos)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"La IA no devolvió un plan válido: {e}")
        archivos = []
        if "xlsx" in formatos:
            archivos.append(generar_excel(plan, p.datos, salida).name)
        if "docx" in formatos:
            archivos.append(generar_word(plan, p.datos, salida).name)
        return {"plan": plan, "archivos": archivos}

    @router.get("/api/informes/archivo/{nombre}")
    def descargar(nombre: str):
        return FileResponse(archivo_seguro(nombre), filename=Path(nombre).name)

    # El correo es un complemento: solo existe si CORREO_ACTIVO=si en el .env
    if (os.environ.get("CORREO_ACTIVO") or "").strip().lower() not in ("si", "sí", "1", "true"):
        return router

    @router.post("/api/asistente")
    def asistente(p: PeticionAsistente):
        """La IA orquesta: elige consulta, Excel, Word o borrador de correo; el código los ejecuta."""
        if completar is None:
            raise HTTPException(503, "La IA no está disponible.")
        if len(p.datos) > MAX_FILAS:
            raise HTTPException(400, f"Demasiadas filas ({len(p.datos)}); máximo {MAX_FILAS}.")
        try:
            return atender(completar, carpeta, p.mensaje, p.datos, p.autor)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"La IA no respondió: {e}")

    @router.post("/api/correo/preparar")
    def preparar_correo(p: PeticionCorreo):
        try:
            adjuntos = [archivo_seguro(n) for n in p.archivos]
            return correo.preparar(ruta_bd, p.para, p.asunto, p.cuerpo, adjuntos, p.autor)
        except correo.ErrorCorreo as e:
            raise HTTPException(400, str(e))

    @router.get("/api/correo/{ident}/vista", response_class=HTMLResponse)
    def vista_correo(ident: str):
        try:
            return correo.vista(ruta_bd, ident)
        except correo.ErrorCorreo as e:
            raise HTTPException(404, str(e))

    @router.post("/api/correo/{ident}/confirmar")
    def confirmar_correo(ident: str):
        try:
            return correo.confirmar(ruta_bd, ident, salida)
        except correo.ErrorCorreo as e:
            raise HTTPException(400, str(e))

    @router.get("/api/correo/historial")
    def historial_correo(limite: int = 50):
        from contextlib import closing
        with closing(correo._bd(ruta_bd)) as con:
            return [dict(f) for f in con.execute(
                "SELECT id, creado, para, asunto, adjuntos, estado, enviado, detalle, autor "
                "FROM correos ORDER BY creado DESC LIMIT ?", (limite,))]

    return router
