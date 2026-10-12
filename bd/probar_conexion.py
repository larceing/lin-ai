"""Prueba de la conexión a A3ERP e Innertia (con la VPN conectada).

Desde la carpeta de la app (donde está app.py):
    .venv\\Scripts\\python.exe -m bd.probar_conexion            conecta y verifica permisos
    .venv\\Scripts\\python.exe -m bd.probar_conexion --todas    además prueba cada consulta del catálogo
    .venv\\Scripts\\python.exe -m bd.probar_conexion --sql a3 "SELECT TOP 5 CODART FROM ARTICULO"
    .venv\\Scripts\\python.exe -m bd.probar_conexion --columnas LINEFACT   columnas de una tabla

Con --sql la consulta pasa por el mismo validador de solo lectura: un UPDATE se rechaza.
Nunca muestra usuario ni contraseña.
"""
import sys
from datetime import date, timedelta

from . import consultas, lectura

HOY = date.today()
EJEMPLOS = {"desde": str(HOY - timedelta(days=30)), "hasta": str(HOY - timedelta(days=1)), "top": "5"}


def tabla(r, maximo=10):
    print("   " + " | ".join(r["columnas"]))
    for f in r["filas"][:maximo]:
        print("   " + " | ".join("" if v is None else str(v) for v in f))
    extra = len(r["filas"]) - maximo
    if extra > 0 or r["truncado"]:
        print(f"   ... {max(extra, 0)} filas más" + (" (truncado)" if r["truncado"] else ""))


def main(args):
    if args[:1] == ["--columnas"] and len(args) == 2:
        try:
            tabla(consultas.ejecutar("a3_columnas", {"tabla": args[1]}, quien="probar_conexion"), 300)
            return 0
        except lectura.ErrorBD as e:
            print(f"ERROR: {e}")
            return 1
    if args[:1] == ["--sql"]:
        if len(args) != 3:
            print('Uso: --sql a3|innertia "SELECT ..."')
            return 2
        try:
            tabla(lectura.ejecutar(args[1], args[2], quien="probar_conexion"), 50)
            return 0
        except lectura.ErrorBD as e:
            print(f"RECHAZADA / ERROR: {e}")
            return 1

    fallos = 0
    for origen, o in lectura.ORIGENES.items():
        print(f"\n== {o['nombre']} · {o['servidor']} · BD {o['bd']}")
        r = lectura.comprobar_permisos(origen)
        print(("  OK   " if r["ok"] else "  FALLO ") + r["motivo"])
        fallos += not r["ok"]

    if "--todas" in args:
        codigo = None
        for c in consultas.listar():
            if lectura.estado()[c["origen"]]["ok"] is not True or c["nombre"] == "a3_columnas":
                continue
            params = {p: EJEMPLOS.get(p, "") for p in c["parametros"]}
            if "articulo" in params and "articulo" not in c["opcionales"]:
                params["articulo"] = codigo or "a"
            print(f"\n-- {c['nombre']} {params}")
            try:
                r = consultas.ejecutar(c["nombre"], params, quien="probar_conexion")
                tabla(r, 5)
                if c["nombre"] == "a3_ventas_periodo" and r["filas"]:
                    codigo = str(r["filas"][0][0])  # un artículo real para probar las demás
            except lectura.ErrorBD as e:
                print(f"   ERROR: {e}")
                fallos += 1

    print("\nResultado:", "todo correcto" if not fallos else f"{fallos} problema(s)")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
