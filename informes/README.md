# Lin-AI · informes y correo

La IA redacta (título, resumen, secciones, qué columnas enseñar, asunto y texto del correo).
El código hace el resto: Excel y Word con los datos reales, y el envío por el correo de
desarrollo de IONOS. La IA nunca escribe las cifras de las tablas.

## La IA como orquestador

`POST /api/asistente` con `{"mensaje": "Hazme un Excel de ventas de septiembre"}`. La IA entiende la
petición y elige las herramientas; cada herramienta es código:

- `consultar`: ejecuta una consulta **predefinida y registrada**; la IA solo elige cuál y sus parámetros
  (no escribe SQL, y los parámetros que no estén declarados se descartan).
- `crear_excel` / `crear_word`: generan el archivo con las filas reales de esa consulta.
- `preparar_correo` (solo con `CORREO_ACTIVO=si`): deja un borrador; una persona lo confirma.

Devuelve la respuesta de la IA, los archivos creados, los borradores y los pasos ejecutados.
El hilo de la base de datos registra sus consultas así:

```python
from informes.orquestador import registrar_consulta
registrar_consulta("ventas_mes", "Ventas por cliente de un mes",
                   {"anio": "integer", "mes": "integer"}, funcion_que_devuelve_filas)
```

## Archivos

- `documentos.py`: genera el `.xlsx` (hoja Datos con todas las filas y hoja Resumen) y el `.docx`
  (texto y tabla con las primeras 50 filas).
- `correo.py`: envío por SMTP de IONOS con lista de destinatarios permitidos, borrador y confirmación.
- `plantilla.py`: plantilla del correo (HTML + texto plano) y resumen automático de cada adjunto.
- `orquestador.py`: la IA elige herramienta (consulta, Excel, Word, correo) y el código la ejecuta.
- `rutas.py`: las rutas web que usa Lin-AI.
- `.env.ejemplo`: las líneas que hay que añadir al `.env`.

## Cómo se usa (API)

1. `POST /api/informes` con `{"peticion": "...", "datos": [ {...}, ... ], "formatos": ["xlsx","docx"]}`
   devuelve el plan de la IA y los nombres de los archivos. Con `"plan": {...}` no se llama a la IA.
2. `GET /api/informes/archivo/{nombre}` descarga un archivo.
Lo siguiente solo existe con `CORREO_ACTIVO=si` en el `.env` (apagado por defecto):

3. `POST /api/correo/preparar` con `{"para": [...], "asunto": "título", "cuerpo": "nota breve", "archivos": [...]}`
   valida y devuelve el borrador, el resumen de los adjuntos y `vista_previa`. No envía nada.
   `asunto` y `cuerpo` son opcionales (ver "Plantilla de correo").
   `GET /api/correo/{id}/vista` enseña el correo tal y como llegará.
4. `POST /api/correo/{id}/confirmar` lo envía (o guarda el `.eml` si `CORREO_ENVIO_REAL` no es `si`).
5. `GET /api/correo/historial` lista lo preparado y enviado.

Hasta que llegue la conexión a la base de datos, `datos` se pasa a mano. Cuando esté, el resultado
de la consulta de solo lectura se convierte con `documentos.datos_desde_cursor(cursor)` y se pasa
como `datos`; también se puede llamar directamente a `generar_excel(plan, datos, carpeta)` y
`generar_word(plan, datos, carpeta)` sin pasar por la web.

## Plantilla de correo

El correo lo monta siempre el código (`plantilla.py`), nunca la IA. Así la IA gasta pocos tokens
y no puede escribir cosas raras. Ejemplo: [vista_correo_ejemplo.png](vista_correo_ejemplo.png).

- Asunto: `[Lin-AI SPSIL] <título> · dd/mm/aaaa`. El título lo da quien prepara el correo (máx.
  90 caracteres, sin HTML ni símbolos); si falta, sale del título del primer adjunto.
- Cuerpo: cabecera SPSIL, saludo, la nota (opcional), una tarjeta por adjunto y el pie
  "Las cifras salen directamente de los archivos adjuntos". Va en HTML y en texto plano.
- Resumen de adjuntos, calculado leyendo cada archivo (0 tokens):
  Excel: filas, columnas y totales de las columnas numéricas (no suma códigos, años ni meses);
  Word: título, apartados y tablas; CSV: filas y columnas; otros: nombre y tamaño.
- Mientras `CORREO_ENVIO_REAL` no sea `si`, el correo lleva la franja "Correo de prueba".

Reglas de la nota (si no se cumplen, el borrador se rechaza con el motivo y la IA lo corrige):
máximo 600 caracteres y 6 líneas; sin HTML ni markdown; sin enlaces; sin direcciones de correo;
sin cifras que no salgan del resumen de los adjuntos (se permiten días, meses y años).
Además, en el asistente la IA solo puede preparar un borrador por petición, solo con archivos
creados en esa misma petición, y nunca lo envía: una persona lo confirma.

## Seguridad

- Usuario y clave del correo solo en el `.env`; no se imprimen ni salen en los errores.
- Solo se envía a `CORREO_PERMITIDOS`; se vuelve a comprobar al confirmar.
- El borrador caduca a los 30 minutos y no se puede enviar dos veces.
- La IA ve columnas, nº de filas, totales calculados por el código y `INFORMES_FILAS_A_IA` filas de muestra.
- La llamada a la IA pasa por el mismo tope de gasto y modelos aprobados que el chat.

## Integración en app.py

Copiar la carpeta `informes/` junto a `app.py`, añadir `openpyxl` y `python-docx` a
`requirements.txt`, y en `app.py`:

```python
from dotenv import dotenv_values, load_dotenv   # añadir load_dotenv
load_dotenv(AQUI / ".env")                      # justo después de AQUI = ...; carga SMTP_*

from informes.rutas import crear_router          # al final, antes de if __name__ ...


def llamar_ia(mensajes):
    if cliente is None:
        raise HTTPException(503, "Falta la clave de OpenAI en el .env")
    if gasto_mes() >= LIMITE_MENSUAL_USD:
        raise HTTPException(429, "Lin-AI ha alcanzado el tope de gasto de este mes. Avisa a Luis.")
    r = preguntar(mensajes, MOTORES["rapido"])
    with closing(bd()) as con, con:
        registrar_consumo(con, "informes", r)
    return r.choices[0].message.content


def completar(mensajes, herramientas):
    if cliente is None:
        raise HTTPException(503, "Falta la clave de OpenAI en el .env")
    if gasto_mes() >= LIMITE_MENSUAL_USD:
        raise HTTPException(429, "Lin-AI ha alcanzado el tope de gasto de este mes. Avisa a Luis.")
    r = cliente.chat.completions.create(model=MOTORES["rapido"], messages=mensajes,
                                        tools=herramientas, max_completion_tokens=MAX_TOKENS_RESPUESTA)
    with closing(bd()) as con, con:
        registrar_consumo(con, "asistente", r)
    return r.choices[0].message


app.include_router(crear_router(AQUI, llamar_ia, completar))
```

Probar el correo sin enviar nada: `python informes\correo.py --probar` (solo inicia sesión en IONOS).
