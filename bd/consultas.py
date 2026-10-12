"""Catálogo cerrado de consultas aprobadas (los "cocineros" de SQL).

La IA nunca escribe SQL ni ejecuta nada: propone una consulta de este catálogo con sus
parámetros, la persona la confirma y el código la ejecuta por lectura.ejecutar (validador +
usuario de solo lectura + ROLLBACK). Para añadir una consulta, se añade aquí y se revisa como
cualquier cambio de código.

Nombres de tablas y columnas de A3 tomados de la vista VW_OFERTAS_STOCK_RAW. Pendiente de
comprobar con la VPN (python -m bd.probar_conexion --todas).
"""
import re
from datetime import date

from . import lectura

# Columna de importe de las líneas de factura (base imponible). SIN VERIFICAR: en A3ERP suele
# ser BASE; si mañana falla, se mira con `python -m bd.probar_conexion --columnas LINEFACT` y se
# cambia en el .env (A3_COL_IMPORTE=...) sin tocar código.
IMPORTE = lectura.CFG.get("A3_COL_IMPORTE") or "BASE"
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", IMPORTE):
    raise ValueError("A3_COL_IMPORTE debe ser un nombre de columna")

# Marcadores: '?' en A3 (SQL Server), '%s' en Innertia (MariaDB).
# Tipos de parámetro: "fecha" (AAAA-MM-DD; al driver le llega como fecha, no como texto, para
# que el idioma del servidor no confunda día y mes). El resto, texto.
# "opcionales": parámetros que pueden ir vacíos (la consulta no filtra por ellos).

# Filtro de artículo: vacío = todos; si no, código exacto o parte de la descripción.
_FILTRO_ARTICULO = "(? = '' OR LTRIM(RTRIM(L.CODART)) = ? OR A.DESCART LIKE ?)"


def _articulo(p):
    a = p.get("articulo", "")
    return (a, a, f"%{a}%")


_PERIODO = {"desde": "Primer día, AAAA-MM-DD", "hasta": "Último día (incluido), AAAA-MM-DD",
            "articulo": "Código o parte de la descripción; vacío = todos"}

CATALOGO = {
    "a3_ventas_periodo": {
        "origen": "a3",
        "descripcion": "Ventas (pedidos de venta) por artículo en un periodo: unidades y nº de pedidos.",
        "parametros": _PERIODO,
        "tipos": {"desde": "fecha", "hasta": "fecha"},
        "opcionales": ["articulo"],
        "sql": "SELECT LTRIM(RTRIM(L.CODART)) AS codigo, MAX(A.DESCART) AS descripcion, "
               "SUM(L.UNIDADES) AS unidades, COUNT(DISTINCT L.IDPEDV) AS pedidos "
               "FROM LINEPEDI L LEFT JOIN ARTICULO A ON A.CODART = L.CODART "
               "WHERE L.IDPEDV IS NOT NULL AND L.FECHA >= ? AND L.FECHA < DATEADD(DAY, 1, ?) "
               f"AND {_FILTRO_ARTICULO} "
               "GROUP BY L.CODART ORDER BY unidades DESC",
        "preparar": lambda p: (p["desde"], p["hasta"]) + _articulo(p),
    },
    "a3_ventas_por_dia": {
        "origen": "a3",
        "descripcion": "Ventas (pedidos de venta) día a día en un periodo: unidades y nº de pedidos.",
        "parametros": _PERIODO,
        "tipos": {"desde": "fecha", "hasta": "fecha"},
        "opcionales": ["articulo"],
        "sql": "SELECT CAST(L.FECHA AS DATE) AS dia, SUM(L.UNIDADES) AS unidades, "
               "COUNT(DISTINCT L.IDPEDV) AS pedidos "
               "FROM LINEPEDI L LEFT JOIN ARTICULO A ON A.CODART = L.CODART "
               "WHERE L.IDPEDV IS NOT NULL AND L.FECHA >= ? AND L.FECHA < DATEADD(DAY, 1, ?) "
               f"AND {_FILTRO_ARTICULO} "
               "GROUP BY CAST(L.FECHA AS DATE) ORDER BY dia",
        "preparar": lambda p: (p["desde"], p["hasta"]) + _articulo(p),
    },
    "a3_facturado_periodo": {
        "origen": "a3",
        "descripcion": "Unidades facturadas (facturas de venta) por artículo en un periodo.",
        "parametros": _PERIODO,
        "tipos": {"desde": "fecha", "hasta": "fecha"},
        "opcionales": ["articulo"],
        "sql": "SELECT LTRIM(RTRIM(L.CODART)) AS codigo, MAX(A.DESCART) AS descripcion, "
               "SUM(L.UNIDADES) AS unidades, COUNT(DISTINCT L.IDFACV) AS facturas "
               "FROM LINEFACT L LEFT JOIN ARTICULO A ON A.CODART = L.CODART "
               "WHERE L.IDFACV IS NOT NULL AND L.FECHA >= ? AND L.FECHA < DATEADD(DAY, 1, ?) "
               f"AND {_FILTRO_ARTICULO} "
               "GROUP BY L.CODART ORDER BY unidades DESC",
        "preparar": lambda p: (p["desde"], p["hasta"]) + _articulo(p),
    },
    # SIN VERIFICAR: CABEFACV.CODREP, CABEFACV.CODCLI y la tabla REPRESEN (CODREP, NOMREP) son
    # los nombres estándar de A3ERP, pero no se han visto en este servidor todavía.
    "a3_ranking_comerciales": {
        "origen": "a3",
        "descripcion": "Ranking de comerciales por lo facturado en un periodo (neto de abonos): "
                       "importe, unidades, facturas y clientes.",
        "parametros": {"desde": "Primer día", "hasta": "Último día (incluido)",
                       "top": "Cuántos comerciales (1-500)", "orden": "importe o unidades"},
        "tipos": {"desde": "fecha", "hasta": "fecha", "top": "entero",
                  "orden": ("importe", "unidades")},
        "opcionales": ["orden"],
        "sql": "SELECT TOP (?) LTRIM(RTRIM(C.CODREP)) AS codigo_comercial, MAX(R.NOMREP) AS comercial, "
               f"SUM(L.{IMPORTE}) AS importe, SUM(L.UNIDADES) AS unidades, "
               "COUNT(DISTINCT C.IDFACV) AS facturas, COUNT(DISTINCT C.CODCLI) AS clientes "
               "FROM LINEFACT L INNER JOIN CABEFACV C ON C.IDFACV = L.IDFACV "
               "LEFT JOIN REPRESEN R ON R.CODREP = C.CODREP "
               "WHERE L.FECHA >= ? AND L.FECHA < DATEADD(DAY, 1, ?) "
               "GROUP BY C.CODREP "
               f"ORDER BY CASE WHEN ? = 'unidades' THEN SUM(L.UNIDADES) ELSE SUM(L.{IMPORTE}) END DESC",
        "preparar": lambda p: (p["top"], p["desde"], p["hasta"], p.get("orden") or "importe"),
    },
    # Familia = ARTICULO.CAR1 (verificado en la vista VW_OFERTAS_STOCK_RAW).
    "a3_ventas_familia": {
        "origen": "a3",
        "descripcion": "Lo facturado por familia de artículo en un periodo (neto de abonos): "
                       "importe, unidades y nº de artículos distintos.",
        "parametros": {"desde": "Primer día", "hasta": "Último día (incluido)",
                       "top": "Cuántas familias (1-500)", "orden": "importe o unidades"},
        "tipos": {"desde": "fecha", "hasta": "fecha", "top": "entero",
                  "orden": ("importe", "unidades")},
        "opcionales": ["orden"],
        "sql": "SELECT TOP (?) ISNULL(A.CAR1, 'FAMILIA DESCONOCIDA') AS familia, "
               f"SUM(L.{IMPORTE}) AS importe, SUM(L.UNIDADES) AS unidades, "
               "COUNT(DISTINCT L.CODART) AS articulos "
               "FROM LINEFACT L LEFT JOIN ARTICULO A ON A.CODART = L.CODART "
               "WHERE L.IDFACV IS NOT NULL AND L.FECHA >= ? AND L.FECHA < DATEADD(DAY, 1, ?) "
               "GROUP BY ISNULL(A.CAR1, 'FAMILIA DESCONOCIDA') "
               f"ORDER BY CASE WHEN ? = 'unidades' THEN SUM(L.UNIDADES) ELSE SUM(L.{IMPORTE}) END DESC",
        "preparar": lambda p: (p["top"], p["desde"], p["hasta"], p.get("orden") or "importe"),
    },
    "a3_stock_articulo": {
        "origen": "a3",
        "descripcion": "Stock por almacén de los artículos que coinciden.",
        "parametros": {"articulo": "Código o parte de la descripción"},
        "sql": "SELECT LTRIM(RTRIM(L.CODART)) AS codigo, MAX(A.DESCART) AS descripcion, "
               "LTRIM(RTRIM(L.CODALM)) AS almacen, SUM(L.UNIDADESSTOCK) AS unidades "
               "FROM STOCKALM L LEFT JOIN ARTICULO A ON A.CODART = L.CODART "
               "WHERE LTRIM(RTRIM(L.CODART)) = ? OR A.DESCART LIKE ? "
               "GROUP BY L.CODART, L.CODALM ORDER BY codigo, almacen",
        "preparar": lambda p: _articulo(p)[1:],
    },
    "a3_pedidos_proveedor_pendientes": {
        "origen": "a3",
        "descripcion": "Unidades pedidas al proveedor y pendientes de recibir.",
        "parametros": {"articulo": "Código o parte de la descripción; vacío = todos"},
        "opcionales": ["articulo"],
        "sql": "SELECT CABEPEDC.IDPEDC AS pedido, LTRIM(RTRIM(L.CODART)) AS codigo, "
               "A.DESCART AS descripcion, L.UNIDADES AS unidades, CABEPEDC.REFERENCIA AS referencia "
               "FROM LINEPEDI L INNER JOIN CABEPEDC ON L.IDPEDC = CABEPEDC.IDPEDC "
               "LEFT JOIN ARTICULO A ON A.CODART = L.CODART "
               "WHERE L.SITUACIONDETALLE LIKE '%PENDIENTE%' AND L.SITUACION = 'A' "
               f"AND {_FILTRO_ARTICULO} "
               "ORDER BY CABEPEDC.IDPEDC",
        "preparar": _articulo,
    },
    "a3_ofertas_stock_articulo": {
        "origen": "a3",
        "descripcion": "Fila del informe de stock (OFERTAS STOCK) de un artículo: stock, reservas, "
                       "ventas recientes, mínimos y máximos.",
        "parametros": {"articulo": "Código exacto del artículo"},
        "sql": "SELECT TOP 1 * FROM dbo.VW_OFERTAS_STOCK_RAW "
               "WHERE LTRIM(RTRIM([Cod. Articulo])) = ?",
        "preparar": lambda p: (p["articulo"],),
    },
    "a3_buscar_articulo": {
        "origen": "a3",
        "descripcion": "Busca artículos por parte del código o de la descripción.",
        "parametros": {"articulo": "Parte del código o de la descripción"},
        "sql": "SELECT TOP 50 LTRIM(RTRIM(CODART)) AS codigo, DESCART AS descripcion, "
               "PRCVENTA AS precio_venta, CODPRO AS proveedor "
               "FROM ARTICULO WHERE DESCART LIKE ? OR CODART LIKE ? ORDER BY DESCART",
        "preparar": lambda p: ("%" + p["articulo"] + "%",) * 2,
    },
    "a3_columnas": {  # para comprobar nombres de columnas con probar_conexion --columnas
        "origen": "a3",
        "descripcion": "Columnas de una tabla o vista de A3ERP.",
        "parametros": {"tabla": "Nombre exacto de la tabla"},
        "sql": "SELECT COLUMN_NAME AS columna, DATA_TYPE AS tipo FROM INFORMATION_SCHEMA.COLUMNS "
               "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
        "preparar": lambda p: (p["tabla"],),
    },
    "innertia_tablas": {
        "origen": "innertia",
        "descripcion": "Lista las tablas de Innertia (para explorar el almacén).",
        "parametros": {},
        "sql": "SELECT table_name AS tabla, table_rows AS filas_aprox "
               "FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name",
        "preparar": lambda p: (),
    },
}

MAX_DIAS = 400  # un periodo más largo se rechaza para no cargar el ERP


def listar():
    return [{"nombre": n, "origen": c["origen"], "descripcion": c["descripcion"],
             "parametros": c["parametros"], "opcionales": c.get("opcionales", [])}
            for n, c in CATALOGO.items()]


def normalizar(nombre: str, parametros: dict | None) -> dict:
    """Comprueba y limpia los parámetros (texto). Lanza ErrorBD si algo no vale."""
    if nombre not in CATALOGO:
        raise lectura.ErrorBD(f"Consulta desconocida: {nombre}.")
    c = CATALOGO[nombre]
    parametros = parametros or {}
    limpio = {}
    for p in c["parametros"]:
        v = parametros.get(p)
        v = "" if v is None else str(v).strip()[:100]
        if not v and p not in c.get("opcionales", []):
            raise lectura.ErrorBD(f"Falta el dato «{p}» para {nombre}.")
        tipo = c.get("tipos", {}).get(p)
        if v and tipo == "fecha":
            try:
                date.fromisoformat(v)
            except ValueError:
                raise lectura.ErrorBD(f"La fecha «{v}» no es válida (AAAA-MM-DD).")
        elif v and tipo == "entero":
            if not v.isdigit() or not 1 <= int(v) <= 500:
                raise lectura.ErrorBD(f"«{p}» debe ser un número entre 1 y 500.")
        elif v and isinstance(tipo, tuple) and v.lower() not in tipo:
            raise lectura.ErrorBD(f"«{p}» debe ser uno de: {', '.join(tipo)}.")
        limpio[p] = v.lower() if isinstance(tipo, tuple) else v
    if limpio.get("desde") and limpio.get("hasta"):
        d, h = date.fromisoformat(limpio["desde"]), date.fromisoformat(limpio["hasta"])
        if d > h:
            raise lectura.ErrorBD("La fecha de inicio es posterior a la de fin.")
        if (h - d).days > MAX_DIAS:
            raise lectura.ErrorBD(f"El periodo es demasiado largo (máximo {MAX_DIAS} días).")
    return limpio


def ejecutar(nombre: str, parametros: dict | None = None, quien: str = "",
             max_filas: int | None = None) -> dict:
    limpio = normalizar(nombre, parametros)
    c = CATALOGO[nombre]
    tipos = c.get("tipos", {})
    tipados = {k: date.fromisoformat(v) if v and tipos.get(k) == "fecha"
               else int(v) if v and tipos.get(k) == "entero" else v
               for k, v in limpio.items()}
    return lectura.ejecutar(c["origen"], c["sql"], c["preparar"](tipados), quien=quien,
                            etiqueta=nombre, max_filas=max_filas)
