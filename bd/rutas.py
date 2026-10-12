"""Rutas de FastAPI para la BD. Se enganchan en app.py con dos líneas:

    from bd.rutas import router as router_bd
    app.include_router(router_bd)

No hay ninguna ruta que acepte SQL libre: solo consultas del catálogo.
"""
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import consultas, lectura

router = APIRouter(prefix="/api/bd", tags=["bd"])


class PeticionConsulta(BaseModel):
    parametros: dict = {}
    quien: str = "anónimo"


@router.get("/estado")
def estado():
    """Qué orígenes hay, si tienen credenciales y si se ha verificado que son de solo lectura."""
    return lectura.estado()


@router.post("/comprobar")
async def comprobar():
    """Conecta a cada origen y verifica permisos (necesita la VPN)."""
    for origen in lectura.ORIGENES:
        await run_in_threadpool(lectura.comprobar_permisos, origen)
    return lectura.estado()


@router.get("/consultas")
def listar():
    return consultas.listar()


@router.post("/consultas/{nombre}")
async def consultar(nombre: str, p: PeticionConsulta):
    try:
        return await run_in_threadpool(consultas.ejecutar, nombre, p.parametros, p.quien)
    except lectura.ConsultaProhibida as e:
        raise HTTPException(403, str(e))
    except lectura.ErrorBD as e:
        raise HTTPException(502, str(e))
