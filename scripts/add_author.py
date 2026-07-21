from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTHOR = "Ing. Oswaldo Cantú Casillas"


def patch_app():
    app = ROOT / "app.py"
    text = app.read_text(encoding="utf-8")

    old = '''c1, c2, c3 = st.columns(3)
    c1.metric("Proyecto", "ASG SPC F09")
    c2.metric("Versión", APP_VERSION)
    c3.metric("Línea", "F09")'''

    new = '''c1, c2, c3, c4 = st.columns(4)
    c1.metric("Proyecto", "ASG SPC F09")
    c2.metric("Versión", APP_VERSION)
    c3.metric("Línea", "F09")
    c4.metric("Autor", "Ing. Oswaldo Cantú Casillas")'''

    if old in text:
        text = text.replace(old, new)

    marker = '''st.info(
        "Proyecto desarrollado como herramienta interna para soporte de producción, "
        "calidad, mantenimiento e ingeniería."
    )'''

    replacement = '''st.markdown(
        """
        **Autor:** Ing. Oswaldo Cantú Casillas  
        **Área:** Ingeniería / Soporte a producción  
        **Línea:** F09
        """
    )

    st.info(
        "Proyecto desarrollado como herramienta interna para soporte de producción, "
        "calidad, mantenimiento e ingeniería."
    )'''

    if "**Autor:** Ing. Oswaldo Cantú Casillas" not in text and marker in text:
        text = text.replace(marker, replacement)

    app.write_text(text, encoding="utf-8")
    print("app.py actualizado con autor.")


def append_if_missing(path: Path, content: str):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")
        print(f"Creado: {path}")
        return

    text = path.read_text(encoding="utf-8")

    if AUTHOR not in text:
        text += "\n\n" + content.strip() + "\n"
        path.write_text(text, encoding="utf-8")
        print(f"Actualizado: {path}")
    else:
        print(f"Ya contiene autor: {path}")


def patch_docs():
    append_if_missing(
        ROOT / "README.md",
        """
## Autor

Ing. Oswaldo Cantú Casillas
        """,
    )

    append_if_missing(
        ROOT / "VERSION.md",
        """
## Autor

Ing. Oswaldo Cantú Casillas
        """,
    )

    append_if_missing(
        ROOT / "docs" / "RESUMEN_EJECUTIVO.md",
        """
## Autor

Ing. Oswaldo Cantú Casillas
        """,
    )

    append_if_missing(
        ROOT / "docs" / "PITCH_GERENCIA.md",
        """
## Autor

Ing. Oswaldo Cantú Casillas
        """,
    )


def main():
    patch_app()
    patch_docs()
    print("Autor agregado correctamente.")


if __name__ == "__main__":
    main()