"""Lin-AI · prueba de correo de nosotros a nosotros mismos. No usa la IA (0 tokens).

Crea un Excel y un Word de ejemplo con datos inventados, monta el correo con la plantilla
y lo manda a una sola dirección (la tuya). Desde la carpeta de Lin-AI (junto a app.py):

    python informes\\probar_envio.py                     -> modo prueba: guarda un .eml, no envía
    python informes\\probar_envio.py --real              -> envío real a MAIL_TO_DEFAULT
    python informes\\probar_envio.py --real yo@spsil.es  -> envío real a esa dirección

La dirección tiene que estar en CORREO_PERMITIDOS (o ser MAIL_TO_DEFAULT del Motor_Analisis).
El envío real pide escribir SI antes de mandar nada. La clave nunca se imprime.
"""
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(AQUI.parent / ".env")

from informes import correo  # noqa: E402
from informes.documentos import generar_excel, generar_word  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    real = "--real" in sys.argv
    cfg = correo.config()
    para = args[0] if args else correo._env_motor().get("MAIL_TO_DEFAULT") or (cfg["permitidos"] or [""])[0]
    if not para or para.startswith("@"):
        sys.exit("Dime a qué dirección mandarlo: python informes\\probar_envio.py [--real] tu@direccion")

    datos = [{"Cliente": f"Cliente de prueba {i}", "Código cliente": 9000 + i,
              "Unidades": i * 4, "Importe": round(i * 87.35, 2)} for i in range(1, 16)]
    plan = {"titulo": "Prueba de correo Lin-AI",
            "resumen": "Informe de prueba con datos inventados para comprobar la plantilla de correo.",
            "secciones": [{"titulo": "Qué se comprueba",
                           "texto": "Que llegan los adjuntos y que el resumen del correo coincide con ellos."}]}
    salida = AQUI.parent / "informes_generados"
    adjuntos = [generar_excel(plan, datos, salida), generar_word(plan, datos, salida)]

    if real:
        os.environ["CORREO_ENVIO_REAL"] = "si"
    try:
        b = correo.preparar(AQUI.parent / "correo.db", [para], "Prueba de correo Lin-AI",
                            "Correo de prueba de nosotros a nosotros. Los datos son inventados.",
                            adjuntos, "prueba")
    except correo.ErrorCorreo as e:
        sys.exit(f"ERROR: {e}")

    print(f"De:      {b['de']}\nPara:    {', '.join(b['para'])}\nAsunto:  {b['asunto']}")
    print(f"Adjuntos: {', '.join(b['adjuntos'])}\nModo:    {b['modo']}\n")
    if real and input("Escribe SI para enviarlo de verdad: ").strip().upper() not in ("SI", "SÍ"):
        sys.exit("Cancelado. No se ha enviado nada.")
    try:
        r = correo.confirmar(AQUI.parent / "correo.db", b["id"], salida)
    except correo.ErrorCorreo as e:
        sys.exit(f"ERROR: {e}")
    if r["estado"] == "enviado":
        print(f"Enviado a {', '.join(r['para'])}. Mira la bandeja de entrada (y la de spam).")
    else:
        print(f"No se ha enviado: guardado en informes_generados\\{r['detalle']}. "
              "Ábrelo con doble clic para verlo en Outlook.")


if __name__ == "__main__":
    main()
