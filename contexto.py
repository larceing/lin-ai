"""Lin-AI · qué fichas se le mandan a la IA.

Antes iban todas las fichas en cada pregunta (unos 3.300 tokens). Ahora va un índice de una
línea por ficha y solo la ficha que más se parece a la pregunta. Sin librerías ni llamadas a
la IA: se cuentan las palabras que la pregunta comparte con cada ficha.
"""
import re
import unicodedata

MAX_FICHAS = int(__import__("os").environ.get("FICHAS_POR_PREGUNTA") or 1)

# Palabras que no ayudan a elegir ficha
VACIAS = set("""
para como cual cuales cuando donde desde hasta sobre entre este esta estos estas
quiero necesito dame saber puedes podrias hacer tiene tienen tengo cuanto cuanta cuantos
cuantas porque pero sino tambien solo todo todos toda todas mismo misma ficha informe
negocio sirve calcula calculan calculo explica
""".split())


def _palabras(texto):
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]{4,}", t) if w not in VACIAS}


def _raiz(w):
    """Singular/plural y género, a lo bruto: 'ventas' y 'venta' cuentan igual."""
    return w[:-2] if w.endswith("es") and len(w) > 5 else w[:-1] if w.endswith(("s", "a", "o")) else w


def _titulo(contenido):
    primera = (contenido or "").strip().splitlines()[0] if (contenido or "").strip() else ""
    return primera.lstrip("# ").replace("Ficha de negocio: ", "")[:100]


def indice(fichas):
    """Una línea por ficha, para que la IA sepa qué existe sin leerlas enteras."""
    return "\n".join(f"- {f['nombre']}: {_titulo(f['contenido'])}" for f in fichas)


def elegir(fichas, texto, maximo=MAX_FICHAS):
    """Las fichas más relacionadas con el texto (ninguna si no se parece a ninguna)."""
    pregunta = {_raiz(w) for w in _palabras(texto)}
    if not pregunta:
        return []
    puntuadas = []
    for f in fichas:
        nombre = {_raiz(w) for w in _palabras(f["nombre"].replace("_", " "))}
        titulo = {_raiz(w) for w in _palabras(_titulo(f["contenido"]))}
        cuerpo = {_raiz(w) for w in _palabras(f["contenido"])}
        puntos = 3 * len(pregunta & nombre) + 2 * len(pregunta & titulo) + len(pregunta & cuerpo)
        if puntos:
            puntuadas.append((puntos, f["nombre"], f))
    puntuadas.sort(key=lambda x: (-x[0], x[1]))
    return [f for _, _, f in puntuadas[:maximo]]


def bloque(fichas, texto, maximo=MAX_FICHAS):
    """Texto de fichas para el mensaje de sistema: índice + la(s) ficha(s) elegida(s)."""
    elegidas = elegir(fichas, texto, maximo)
    partes = ["Fichas disponibles:\n" + indice(fichas)]
    partes += [f"### FICHA: {f['nombre']}\n{f['contenido']}" for f in elegidas]
    if not elegidas:
        partes.append("(Ninguna ficha encaja con la pregunta; si necesitas una, dilo.)")
    return "\n\n".join(partes)


# En un pedido de datos la ficha casi nunca hace falta: solo se manda si la pregunta pide
# una explicación ("¿cómo se calcula…?", "¿qué es…?"). Si no, basta con el índice.
_EXPLICACION = re.compile(r"(?i)\b(c[oó]mo|por\s*qu[eé]|qu[eé]\s+es|qu[eé]\s+significa|calcul|explica|defin)")


def bloque_pedido(fichas, texto):
    if _EXPLICACION.search(texto or ""):
        return bloque(fichas, texto)
    return "Fichas disponibles (si te preguntan cómo se calcula algo, dilo y te la paso):\n" + indice(fichas)
