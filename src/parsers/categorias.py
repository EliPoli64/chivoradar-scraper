import re
from typing import Any

SIN_CATEGORIA = "Sin Categoría"

_ACCENTOS = str.maketrans(
    "áéíóúüñÁÉÍÓÚÜÑ",
    "aeiouunAEIOUUN",
)

CATEGORIAS: list[tuple[str, tuple[str, ...]]] = [
    ("Aventura/Campamentos", ("campamento", "aventura", "excursion", "buceo", "hiking", "senderismo")),
    ("Conciertos", ("concierto", "dj", "techno", "tecno", "banda", "rock", "reggaeton", "sing along", "en vivo", "gira")),
    ("Teatro/Cine", ("teatro", "musical", "funcion", "cine", "obra teatral")),
    ("Deportes", ("torneo", "campeonato", "carrera", "maraton", "boxeo", "box", "ciclismo", "futbol", "baloncesto", "tenis")),
    ("Gastronomía", ("cena", "almuerzo", "chef", "gastro", "degustacion", "vino", "menu", "cocina")),
    ("Conferencias/Educación", ("congreso", "seminario", "workshop", "taller", "conferencia", "charla", "masterclass")),
    ("Festivales", ("festival", "feria")),
]


def normalizar(texto: str) -> str:
    texto = texto.lower().translate(_ACCENTOS)
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _contiene(señal: str, clave: str) -> bool:
    if len(clave) <= 4:
        return re.search(rf"(^| ){re.escape(clave)}( |$)", señal) is not None
    return clave in señal


def _extraerTexto(valor: Any) -> list[str]:
    textos: list[str] = []
    if isinstance(valor, str):
        textos.append(valor)
    elif isinstance(valor, dict):
        for clave in ("name", "eventName", "performerName"):
            if valor.get(clave):
                textos.append(str(valor[clave]))
        if not textos:
            textos.append(" ".join(str(v) for v in valor.values() if isinstance(v, (str, int, float))))
    elif isinstance(valor, list):
        for item in valor:
            textos.extend(_extraerTexto(item))
    return textos


def construirSeñal(datos: dict) -> str:
    partes: list[str] = []
    for clave in ("name", "titulo", "title", "eventName", "description", "descripcion", "eventDetail", "offers", "organizer", "performer"):
        if clave in datos:
            partes.extend(_extraerTexto(datos[clave]))
    return " ".join(partes)


def inferirCategoria(datos: dict) -> str:
    señal = normalizar(construirSeñal(datos))
    if not señal:
        return SIN_CATEGORIA
    for categoria, claves in CATEGORIAS:
        if any(_contiene(señal, clave) for clave in claves):
            return categoria
    return SIN_CATEGORIA


def esCategoriaUtil(categoria: str | None) -> bool:
    return bool(categoria and categoria.strip() and categoria.strip() != SIN_CATEGORIA)