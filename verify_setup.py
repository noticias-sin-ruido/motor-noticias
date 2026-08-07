#!/usr/bin/env python3
"""
Script de verificación del setup de Sin Ruido Fase 1.
Valida que:
  1. Todas las dependencias están instaladas
  2. Los modelos se pueden importar sin errores
  3. La conexión a la BD (en memoria) funciona
  4. La app FastAPI se puede instanciar
"""
import sys
from datetime import datetime


def check_imports():
    """Verifica que se pueden importar todos los módulos necesarios."""
    print("🔍 Verificando imports...")
    try:
        import fastapi
        print(f"  ✓ fastapi {fastapi.__version__}")

        import sqlmodel
        print(f"  ✓ sqlmodel {sqlmodel.__version__}")

        import pydantic
        print(f"  ✓ pydantic {pydantic.__version__}")

        import sqlalchemy
        print(f"  ✓ sqlalchemy {sqlalchemy.__version__}")

        from pgvector.sqlalchemy import Vector
        print(f"  ✓ pgvector")

        import uvicorn
        print(f"  ✓ uvicorn {uvicorn.__version__}")

        return True
    except ImportError as e:
        print(f"  ✗ Error de import: {e}")
        return False


def check_models():
    """Verifica que los modelos se pueden instanciar."""
    print("\n🔍 Verificando modelos...")
    try:
        from src.models import Medio, Noticia, Cluster, Sintesis

        # Instanciar Medio
        medio = Medio(
            nombre="Test Medio",
            url_base="https://test.com",
            feed_rss="https://test.com/rss",
        )
        print(f"  ✓ Medio creado: {medio.nombre}")

        # Instanciar Cluster
        cluster = Cluster(titulo_evento="Test Event", estado="abierto")
        print(f"  ✓ Cluster creado: {cluster.titulo_evento}")

        # Instanciar Noticia
        noticia = Noticia(
            medio_id=1,
            titulo="Test News",
            url="https://test.com/news",
            guid="guid-test-news",
            contenido_limpio="Test content",
            fecha_publicacion=datetime.utcnow(),
        )
        print(f"  ✓ Noticia creada: {noticia.titulo}")

        # Instanciar Sintesis
        sintesis = Sintesis(
            cluster_id=1,
            resumen_neutro="Test summary",
            puntos_clave=["Key 1", "Key 2"],
        )
        print(f"  ✓ Sintesis creada: {sintesis.resumen_neutro}")

        return True
    except Exception as e:
        print(f"  ✗ Error en modelos: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_database():
    """Verifica que la conexión a BD (en memoria) funciona."""
    print("\n🔍 Verificando base de datos...")
    try:
        from sqlmodel import Session, SQLModel, create_engine, select
        from src.models import Medio

        # Crear engine en memoria
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        # Crear tablas
        SQLModel.metadata.create_all(engine)
        print("  ✓ Tablas creadas en BD en memoria")

        # Insertar un Medio
        with Session(engine) as session:
            medio = Medio(
                nombre="Test Medio",
                url_base="https://test.com",
                feed_rss="https://test.com/rss",
            )
            session.add(medio)
            session.commit()
            session.refresh(medio)
            print(f"  ✓ Medio insertado (ID: {medio.id})")

            # Recuperar
            statement = select(Medio).where(Medio.nombre == "Test Medio")
            medio_recuperado = session.exec(statement).first()
            if medio_recuperado:
                print(f"  ✓ Medio recuperado: {medio_recuperado.nombre}")
            else:
                raise Exception("No se pudo recuperar el Medio")

        return True
    except Exception as e:
        print(f"  ✗ Error en BD: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_api():
    """Verifica que la app FastAPI se puede instanciar."""
    print("\n🔍 Verificando API FastAPI...")
    try:
        from src.main import app
        from fastapi.testclient import TestClient

        print(f"  ✓ App FastAPI instanciada: {app.title}")

        # Crear cliente de test
        client = TestClient(app)

        # Hacer request a /
        response = client.get("/")
        if response.status_code == 200:
            print(f"  ✓ GET / respondió con 200: {response.json()}")
        else:
            print(f"  ✗ GET / respondió con {response.status_code}")
            return False

        return True
    except Exception as e:
        print(f"  ✗ Error en API: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Ejecuta todas las verificaciones."""
    print("=" * 60)
    print("🚀 VERIFICACIÓN DEL SETUP: Sin Ruido Fase 1")
    print("=" * 60)

    checks = [
        ("Imports", check_imports),
        ("Modelos", check_models),
        ("Base de Datos", check_database),
        ("API FastAPI", check_api),
    ]

    results = []
    for name, check in checks:
        result = check()
        results.append((name, result))

    print("\n" + "=" * 60)
    print("📊 RESUMEN")
    print("=" * 60)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} — {name}")

    print("=" * 60)

    all_passed = all(result for _, result in results)
    if all_passed:
        print("\n🎉 ¡TODAS LAS VERIFICACIONES PASARON! La API está lista.")
        return 0
    else:
        print("\n⚠️  Algunas verificaciones fallaron. Revisa los errores arriba.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
