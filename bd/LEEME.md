# Lin-AI · conexión a A3ERP e Innertia en solo lectura

Módulo aparte (`bd/`) para que Lin-AI lea datos reales. La IA no escribe SQL: propone una consulta
del catálogo cerrado (`consultas.py`), la persona confirma y el código la ejecuta, validada.

## Invariantes (fijos, no se pueden desactivar desde el .env)

| | Regla | Cómo se garantiza |
|---|---|---|
| I1 | Solo una sentencia `SELECT` / `WITH … SELECT` | `validar_sql()` rechaza UPDATE, DELETE, INSERT, MERGE, ALTER, CREATE, DROP, TRUNCATE, EXEC, `SELECT INTO`, `FOR UPDATE`, comentarios, `;` intermedios, procedimientos `xp_`/`sp_`, OPENROWSET… antes de abrir conexión |
| I2 | El usuario de BD debe ser de solo lectura | Al conectar se leen sus permisos (roles y permisos por tabla en SQL Server, `SHOW GRANTS` en MariaDB). Si puede escribir, ese origen queda bloqueado |
| I3 | Nunca se confirma nada | Cada consulta va en una transacción que termina en ROLLBACK. En MariaDB, además, la sesión es READ ONLY: el propio servidor rechaza escrituras (probado) |
| I4 | Credenciales solo en el .env | Nunca se imprimen ni salen en errores ni en la auditoría |
| I5 | Todo queda registrado | `bd_auditoria.jsonl` junto a app.py: quién, qué consulta, filas, error |
| I6 | No cargar el ERP | Máximo 500 filas y 30 s por consulta; en A3 lectura sin bloqueos (READ UNCOMMITTED, como los informes de A3) |

No hay ninguna ruta web que acepte SQL libre. El SQL libre solo existe en `probar_conexion.py`,
en la consola, y pasa por el mismo validador.

## Prueba de mañana (con VPN)

1. Copiar `app.py`, `index.html`, `requirements.txt`, `iniciar.bat` y las carpetas `bd/`, `carta/` e `informes/` de `lin-ai/` a la carpeta de la app.
2. Añadir al `.env` de la app las líneas de `bd/env.ejemplo` con el usuario y la clave de lectura.
3. Abrir `iniciar.bat` una vez: instala las librerías nuevas y arranca. Cerrarlo para seguir.
4. Para A3 hace falta un driver ODBC de SQL Server. Si el paso 5 dice que no hay ninguno,
   instalar "ODBC Driver 18 for SQL Server" de Microsoft.
5. Desde la carpeta de la app:
   - `.venv\Scripts\python.exe -m bd.probar_conexion` conecta y verifica que el usuario es de solo lectura.
   - `.venv\Scripts\python.exe -m bd.probar_conexion --todas` prueba también cada consulta del catálogo.
   - `.venv\Scripts\python.exe -m bd.probar_conexion --sql a3 "SELECT TOP 5 CODART, DESCART FROM ARTICULO"`
6. Pruebas sin red: `.venv\Scripts\python.exe -m unittest bd.test_bd -v`

### Las dos pruebas de Luis (desde la pantalla de Lin-AI)

1. **«Necesito un Excel de los 24 mejores comerciales».** Debe proponer directamente
   «Ranking de comerciales · del 01/01/2026 a hoy · los 24 primeros · Excel», sin preguntas
   (sin periodo, un ranking usa el año en curso y lo dice). Se pulsa «Prepararlo» y sale el
   Excel con datos reales. Coste: una sola llamada a la IA.
2. **«¿Qué familias han vendido más?».** Debe preguntar con 3 opciones (por ejemplo: este año
   por importe, último mes por importe, este año por unidades). Tras elegir una, propone el
   plato y el programa pregunta «¿Lo quieres en Excel, en Word o te lo enseño aquí?» sin otra
   llamada a la IA. Coste: dos llamadas a la IA.

**Sin verificar** (comprobar antes de las pruebas):
- Columna de importe de las líneas de factura: se usa `LINEFACT.BASE`. Comprobar con
  `.venv\Scripts\python.exe -m bd.probar_conexion --columnas LINEFACT`; si se llama distinto,
  poner `A3_COL_IMPORTE=<nombre>` en el .env (no hace falta tocar código).
- Comerciales: `CABEFACV.CODREP`, `CABEFACV.CODCLI` y la tabla `REPRESEN` (`CODREP`, `NOMREP`).
  Comprobar con `--columnas CABEFACV` y `--columnas REPRESEN`.
- Verificado en la vista de stock: `LINEFACT` (`IDFACV`, `FECHA`, `CODART`, `UNIDADES`) y la
  familia `ARTICULO.CAR1`.

Si una consulta del catálogo falla por un nombre de columna, es que hay que ajustarla en
`consultas.py` (los nombres de A3 vienen de la vista `VW_OFERTAS_STOCK_RAW`; los de Innertia aún
no se conocen, por eso de momento solo está `innertia_tablas`).

## Cómo lo usa Lin-AI (ya integrado en `lin-ai/app.py`)

La IA es el camarero y el código, los cocineros (`carta/carta.py`):
1. La persona pide en sus palabras: «ventas de la última semana en Excel».
2. La IA interpreta (qué ventas, qué artículos, fechas reales, formato), pregunta si hace falta
   y **propone** un plato de la carta. No ejecuta nada.
3. La persona pulsa «Prepararlo» (puede cambiar el formato) o «No es esto».
4. El código ejecuta la consulta del catálogo en solo lectura y devuelve tabla, Excel o Word.
5. Cada pedido confirmado o rechazado se guarda (tabla `pedidos` de datos.db) y se le enseña a
   la IA en el siguiente pedido para que acierte antes.

La IA nunca escribe SQL: solo elige del catálogo cerrado (`consultas.py`).

## Archivos

- `lectura.py` conexión, validador, verificación de permisos, auditoría
- `consultas.py` catálogo de consultas aprobadas y herramientas para la IA
- `rutas.py` rutas de FastAPI (`/api/bd/estado`, `/api/bd/comprobar`, `/api/bd/consultas`)
- `probar_conexion.py` prueba en consola
- `test_bd.py` pruebas de los invariantes (sin red)
- `env.ejemplo`, `requirements_bd.txt`
