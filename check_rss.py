"""
Script temporal de prueba: compara 'description' (summary) contra
'content:encoded' en los RSS de Clarin y TN, para ver si alguno de
los dos trae el cuerpo completo del articulo o solo un teaser corto.
Ademas, imprime los titulos de todos los items disponibles en el feed
para relevar las frases que usa cada medio en notas "en vivo" /
minuto a minuto (Fase 2 -- exclusion de liveblogs).

Es descartable: se puede borrar despues de usarlo.
"""
import feedparser

feeds = {
    "El cronista": "https://www.cronista.com/files/rss/news.xml",
}

# Palabras/frases candidatas a heuristico de "en vivo" -- si un titulo las
# contiene (sin importar mayusculas/minusculas), se marca para revisar.
PISTAS_EN_VIVO = ["vivo", "minuto a minuto", "live", "ultima hora", "en directo"]


def parece_en_vivo(titulo: str) -> bool:
    titulo_lower = titulo.lower()
    return any(pista in titulo_lower for pista in PISTAS_EN_VIVO)


for nombre, url in feeds.items():
    print(f"\n{'=' * 60}")
    print(f"{nombre} -- {url}")
    print("=" * 60)

    f = feedparser.parse(url)

    if f.bozo:
        print(f"Aviso al parsear: {f.bozo_exception}")

    print(f"Status HTTP: {f.get('status', 'desconocido')}")
    print(f"Cantidad de items encontrados: {len(f.entries)}")

    if not f.entries:
        print("No se encontraron entradas. Puede que la URL del feed este mal.")
        continue

    print(f"\nTitulos de todos los items ({len(f.entries)}):")
    for i, entry in enumerate(f.entries):
        titulo = entry.get("title", "N/A")
        marca = " <-- posible EN VIVO" if parece_en_vivo(titulo) else ""
        print(f"  [{i + 1}] {titulo}{marca}")

    print("\nDetalle de contenido (primeros 2 items):")
    for i, entry in enumerate(f.entries[:2]):
        print(f"\n--- Item {i + 1} ---")
        print(f"Titulo: {entry.get('title', 'N/A')}")

        summary = entry.get("summary", "")
        print(f"Description/summary ({len(summary)} caracteres):")
        print(f"  {summary[:150]}")

        content_list = entry.get("content")
        if content_list:
            content_text = content_list[0].get("value", "")
            print(f"content:encoded ENCONTRADO ({len(content_text)} caracteres):")
            print(f"  {content_text[:300]}")
        else:
            print("content:encoded NO encontrado -- solo hay summary/description.")