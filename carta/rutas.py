"""Rutas web de la carta. En app.py:

    from carta.rutas import crear_router as router_carta
    app.include_router(router_carta(AQUI, BD, completar, texto_fichas))
"""
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

import textos
from bd import lectura

from . import carta


class Peticion(BaseModel):
    conversacion: str
    mensaje: str
    autor: str = "anónimo"
    idioma: str = "es"  # "es", "zh" o "en"


class Confirmacion(BaseModel):
    formato: str | None = None
    autor: str = "anónimo"
    idioma: str = "es"
    canal: str | None = None       # "pantalla" o "correo"; si falta, el que propuso la IA
    para: list[str] | None = None  # destinatarios; si falta, los de la propuesta o el por defecto


class Rechazo(BaseModel):
    motivo: str = ""
    idioma: str = "es"


def crear_router(carpeta: Path, ruta_bd: Path, completar: Callable, fichas: Callable[[str], str]):
    carta.preparar_bd(ruta_bd)
    router = APIRouter(prefix="/api", tags=["carta"])

    @router.get("/carta")
    def ver_carta(idioma: str = "es"):
        id_ = textos.idioma(idioma)
        return {"platos": {n: textos.plato(n, id_) for n in carta.PLATOS},
                "formatos": {f: textos.formato(f, id_) for f in carta.FORMATOS},
                "idiomas": textos.IDIOMAS}

    @router.post("/pedido")
    def pedir(p: Peticion):
        id_ = textos.idioma(p.idioma)
        if not p.mensaje.strip():
            raise HTTPException(400, textos.t("mensaje_vacio", id_))
        return carta.atender(ruta_bd, completar, p.conversacion, p.mensaje.strip()[:2000],
                             p.autor, fichas(p.mensaje), id_, carpeta)

    @router.post("/pedido/{ident}/confirmar")
    async def confirmar(ident: str, c: Confirmacion):
        id_ = textos.idioma(c.idioma)
        try:
            return await run_in_threadpool(carta.cocinar, ruta_bd, carpeta, ident, c.formato, c.autor, id_,
                                           c.canal, c.para)
        except carta.AvisoPedido as e:
            raise HTTPException(400, str(e))
        except lectura.ConsultaProhibida as e:
            print(f"Consulta rechazada en el pedido {ident}: {e}")
            raise HTTPException(403, textos.t("consulta_no_permitida", id_))
        except lectura.ErrorBD as e:
            # El detalle técnico (VPN, usuario, columna...) va a la consola y a bd_auditoria.jsonl;
            # la persona solo ve que falla la conexión a los datos.
            print(f"Fallo de datos en el pedido {ident}: {e}")
            raise HTTPException(502, textos.t("sin_conexion_datos", id_))

    @router.post("/pedido/{ident}/rechazar")
    def no_es_esto(ident: str, r: Rechazo):
        try:
            return carta.rechazar(ruta_bd, ident, r.motivo, textos.idioma(r.idioma))
        except lectura.ErrorBD as e:
            raise HTTPException(400, str(e))

    @router.get("/pedido/aprendizajes")
    def aprendido(limite: int = 50):
        return carta.aprendizajes(ruta_bd, limite)

    return router
