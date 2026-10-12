"""Pruebas sin red de los invariantes de solo lectura.

Desde la carpeta de la app:  python -m unittest bd.test_bd -v
"""
import json
import sys
import unittest
from unittest import mock

from . import consultas, lectura

PERMITIDAS = [
    "SELECT TOP 5 CODART FROM ARTICULO",
    "select codart, descart from articulo where descart like '%DELETE%';",
    "SELECT [Update], REPLACE(DESCART, 'a', 'b') FROM ARTICULO WITH (NOLOCK)",
    "WITH c AS (SELECT CODART FROM STOCKALM) SELECT * FROM c",
    "(SELECT 1) UNION (SELECT 2)",
    "SELECT 'texto con ; y -- dentro' AS x",
    "SELECT `update` FROM hueco WHERE nombre = 'O''Brien'",
]
PROHIBIDAS = [
    "UPDATE ARTICULO SET DESCART = 'x'",
    "DELETE FROM ARTICULO",
    "INSERT INTO ARTICULO VALUES (1)",
    "ALTER VIEW dbo.V AS SELECT 1",
    "DROP TABLE ARTICULO",
    "TRUNCATE TABLE STOCKALM",
    "MERGE ARTICULO USING x ON 1=1 WHEN MATCHED THEN DELETE;",
    "SELECT 1; DELETE FROM ARTICULO",
    "SELECT * INTO copia FROM ARTICULO",
    "WITH c AS (SELECT 1 AS a) UPDATE ARTICULO SET PRCVENTA = 0",
    "EXEC xp_cmdshell 'dir'",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1')",
    "SELECT 1 /*! ; DELETE FROM hueco */",
    "SELECT 1 -- ; DROP TABLE x",
    "SELECT 1 # comentario",
    "SELECT * FROM hueco FOR UPDATE",
    "SELECT * FROM hueco LOCK IN SHARE MODE",
    "SELECT SLEEP(100)",
    "SELECT * FROM hueco INTO OUTFILE '/tmp/x'",
    "SELECT 'a\\'; DELETE FROM hueco; -- '",
    "SELECT 'sin cerrar",
    "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE",
    "USE master",
    "DECLARE @x INT",
    "SELECT * FROM sys.objects WHERE 1 = sp_executesql",
    "  ",
    "SHOW GRANTS",
    "GRANT SELECT ON x TO y",
    "BACKUP DATABASE SG TO DISK = 'x'",
    "SELECT * FROM t WAITFOR DELAY '00:00:10'",
]


class Validador(unittest.TestCase):
    def test_permitidas(self):
        for sql in PERMITIDAS:
            with self.subTest(sql=sql):
                lectura.validar_sql(sql)

    def test_prohibidas(self):
        for sql in PROHIBIDAS:
            with self.subTest(sql=sql):
                with self.assertRaises(lectura.ConsultaProhibida):
                    lectura.validar_sql(sql)

    def test_catalogo_pasa_el_validador(self):
        for nombre, c in consultas.CATALOGO.items():
            with self.subTest(nombre=nombre):
                lectura.validar_sql(c["sql"])


class CursorFalso:
    def __init__(self, con):
        self.con, self.description = con, None

    def execute(self, sql, params=()):
        self.con.ejecutadas.append(sql)
        if "HAS_PERMS_BY_NAME" in sql:
            self.description = [(c,) for c in self.con.permisos]
            self._filas = [tuple(self.con.permisos.values())]
        elif sql.startswith("SHOW GRANTS"):
            self.description = [("g",)]
            self._filas = [(g,) for g in self.con.concesiones]
        elif sql.lstrip().upper().startswith(("SELECT", "WITH")):
            self.description = [("a",), ("b",)]
            self._filas = [(i, f"fila {i}   ") for i in range(1000)]
        else:
            self.description = None

    def fetchone(self):
        return self._filas[0]

    def fetchall(self):
        return self._filas

    def fetchmany(self, n):
        return self._filas[:n]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class ConexionFalsa:
    def __init__(self, permisos=None, concesiones=()):
        self.ejecutadas, self.rollbacks, self.commits, self.cerrada = [], 0, 0, False
        self.permisos = permisos or {"sysadmin": 0, "db_owner": 0, "db_datawriter": 0,
                                     "db_ddladmin": None, "bd_insert": 0, "tablas_escribibles": 0}
        self.concesiones = list(concesiones) or ["GRANT SELECT ON `almacenmultiforte`.* TO `lec`@`%`"]
        self.timeout = 0

    def cursor(self):
        return CursorFalso(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):  # nunca debe llamarse
        self.commits += 1

    def close(self):
        self.cerrada = True


class Ejecucion(unittest.TestCase):
    def setUp(self):
        lectura._estado.clear()
        self.cred = mock.patch.dict(lectura.ORIGENES["a3"], usuario="lector", clave="S3cr3t0!")
        self.cred_inn = mock.patch.dict(lectura.ORIGENES["innertia"], usuario="lec", clave="Otra#Clave")
        self.audit = mock.patch.object(lectura, "AUDITORIA", lectura.Path(self._tmp()))
        for p in (self.cred, self.cred_inn, self.audit):
            p.start()
            self.addCleanup(p.stop)

    def _tmp(self):
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        return f.name

    def _con(self, **kw):
        con = ConexionFalsa(**kw)
        p = mock.patch.object(lectura, "_conectar", return_value=con)
        p.start()
        self.addCleanup(p.stop)
        return con

    def test_lectura_ok_siempre_rollback_y_limite(self):
        con = self._con()
        r = lectura.ejecutar("a3", "SELECT a, b FROM T")
        self.assertEqual(len(r["filas"]), lectura.MAX_FILAS)
        self.assertTrue(r["truncado"])
        self.assertEqual(r["filas"][0][1], "fila 0")
        self.assertGreaterEqual(con.rollbacks, 1)
        self.assertEqual(con.commits, 0)
        self.assertTrue(con.cerrada)

    def test_prohibida_no_toca_la_red(self):
        with mock.patch.object(lectura, "_conectar") as conectar:
            with self.assertRaises(lectura.ConsultaProhibida):
                lectura.ejecutar("a3", "UPDATE ARTICULO SET PRCVENTA = 0")
            conectar.assert_not_called()

    def test_usuario_con_escritura_bloquea_a3(self):
        self._con(permisos={"sysadmin": 0, "db_owner": 0, "db_datawriter": 1, "tablas_escribibles": 12})
        with self.assertRaisesRegex(lectura.ErrorBD, "PUEDE escribir"):
            lectura.ejecutar("a3", "SELECT 1")
        self.assertFalse(lectura.estado()["a3"]["ok"])

    def test_usuario_con_escritura_bloquea_innertia(self):
        self._con(concesiones=["GRANT SELECT, INSERT, UPDATE ON `almacenmultiforte`.* TO `lec`@`%`"])
        with self.assertRaisesRegex(lectura.ErrorBD, "PUEDE escribir"):
            lectura.ejecutar("innertia", "SELECT 1")

    def test_innertia_transaccion_read_only(self):
        con = self._con()
        lectura.ejecutar("innertia", "SELECT a, b FROM hueco")
        self.assertIn("START TRANSACTION READ ONLY", con.ejecutadas)

    def test_credenciales_no_salen_en_errores(self):
        con = self._con()
        con.cursor = lambda: (_ for _ in ()).throw(RuntimeError("Login failed lector PWD=S3cr3t0!"))
        with self.assertRaises(lectura.ErrorBD) as e:
            lectura.ejecutar("a3", "SELECT 1")
        self.assertNotIn("S3cr3t0!", str(e.exception))
        self.assertNotIn("lector", str(e.exception))
        auditoria = lectura.AUDITORIA.read_text(encoding="utf-8")
        self.assertNotIn("S3cr3t0!", auditoria)

    def test_auditoria(self):
        self._con()
        lectura.ejecutar("a3", "SELECT a, b FROM T", quien="karin", etiqueta="prueba")
        lineas = [json.loads(l) for l in lectura.AUDITORIA.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lineas[-1]["quien"], "karin")
        self.assertEqual(lineas[-1]["filas"], lectura.MAX_FILAS)

    def test_sin_credenciales(self):
        with mock.patch.dict(lectura.ORIGENES["a3"], usuario="", clave=""):
            with self.assertRaisesRegex(lectura.ErrorBD, "A3_USUARIO"):
                lectura._conectar("a3")

    def test_catalogo_parametros(self):
        con = self._con()
        r = consultas.ejecutar("a3_ventas_periodo", {"desde": "2026-09-28", "hasta": "2026-10-04"})
        self.assertEqual(len(r["filas"]), lectura.MAX_FILAS)
        r = consultas.ejecutar("a3_ventas_periodo", {"desde": "2026-09-28", "hasta": "2026-10-04"},
                               max_filas=10**9)
        self.assertEqual(len(r["filas"]), 1000)  # el falso devuelve 1000; el tope es 20.000
        for malos, error in [({"desde": "28/09/2026", "hasta": "2026-10-04"}, "no es válida"),
                             ({"desde": "2026-10-05", "hasta": "2026-10-04"}, "posterior"),
                             ({"desde": "2020-01-01", "hasta": "2026-10-04"}, "demasiado largo"),
                             ({"hasta": "2026-10-04"}, "Falta")]:
            with self.assertRaisesRegex(lectura.ErrorBD, error):
                consultas.ejecutar("a3_ventas_periodo", malos)
        with self.assertRaisesRegex(lectura.ErrorBD, "desconocida"):
            consultas.ejecutar("borrar_todo", {})


if __name__ == "__main__":
    unittest.main()
