# Pruebas manuales del bloque F — pendientes

Escrito el 11/09/2026, al cerrar F1, F2 y F3. **Nada de esto lo ve una suite**, y
ese es el criterio con el que está armada: la ronda manual anterior sacó cinco
defectos que las tres suites daban por buenos, y los cinco compartían forma —
escenarios que ningún test monta.

Para arrancar: levantar Docker, después `npm run tauri dev` desde `app/`. La app
arranca los contenedores sola.

## 1. El arranque, y la pantalla nueva

- [ ] La app arranca los contenedores y muestra `arrancando → migrando → listo`.
- [ ] La pestaña **Medios** carga sola, sin apretar nada: la lista y los números
      de composición llegan juntos (`Promise.all`).
- [ ] Cada fila muestra **los feeds enteros**, no "1 feed".
- [ ] La barra de proporción se dibuja. Si no se ve nada, el `data-proporcion`
      no está encontrando su regla — es el modo de falla de esa técnica.

## 2. Los campos, en los dos temas

El arreglo de los campos **era un bug de tema oscuro**: un `<input>` sin `type`
quedaba blanco puro sobre la tarjeta azul. Así que hay que mirarlo en los dos.

- [ ] Tema oscuro: los campos de *Sumar medio* y de *Editar* se ven **hundidos**
      respecto de la tarjeta, no flotando.
- [ ] El `textarea` de feeds, igual que los `input`.
- [ ] Tema claro: lo mismo, sin que ninguno quede blanco sobre blanco.
- [ ] El buscador y el selector de orden **alineados**, misma altura.
- [ ] El buscador en tipografía normal, no monoespaciada.

## 3. Buscar y ordenar

- [ ] Buscar por nombre filtra.
- [ ] Buscar por una parte de la **URL de un feed** también filtra.
- [ ] Un término sin resultados muestra el mensaje y no una lista vacía muda.
- [ ] Ordenar por "material sin publicar" pone a La Nación arriba.

## 4. El alta, con su modal

- [ ] Al apretar *Sumar medio* aparece **primero el modal de términos**, antes de
      que se guarde nada.
- [ ] Cancelar no da de alta.
- [ ] Aceptar da de alta y muestra el informe del sondeo.
- [ ] Un feed inventado devuelve 422 con el motivo, y **no guarda**.
- [ ] Pegar **dos feeds en dos renglones** y confirmar que entran los dos. Es lo
      que se creyó que no existía.

## 5. La edición y la guarda de atribución

Es la prueba más importante del bloque: lo que evita es que una síntesis salga
**firmada** atribuyendo a un medio algo que publicó otro.

- [ ] Cambiar sólo el nombre: guarda **sin salir a la red** (no tarda).
- [ ] Cambiar la ruta del feed dentro del mismo host: guarda directo.
- [ ] Apuntar el feed a **otro dominio**: aparece la repregunta **nombrando los
      hosts**, y si se cancela no se guarda nada.
- [ ] Confirmar: recién ahí guarda.
- [ ] Ponerle a un medio el nombre de otro: 409 con el nombre, no "el motor
      respondió 409".

## 6. El escenario que rompió todo la vez pasada

**Un motor con la API abierta y la app sin credencial.** No existe en ninguna
suite —los tests de Rust corren contra un almacén que siempre tiene token, los
del motor corren sin app del otro lado— y es el de cualquiera que instale esto
por primera vez.

- [ ] Con `API_TOKEN` comentado en el `.env` (lo hace el usuario), reiniciar el
      contenedor y confirmar que **la pestaña Medios funciona igual**.
- [ ] Restaurar el `.env` después.

## 7. Lo de siempre, que ya falló una vez

- [ ] **La ventana se puede cerrar** desde la pestaña Medios, y con un modal
      abierto.
- [ ] Escape cierra los modales; el foco vuelve a donde estaba.

## 8. Contra el dato, no contra la pantalla

- [ ] Los números del panel coinciden con SQL. Para el total:

```sql
WITH cm AS (
  SELECT c.id AS cid, COUNT(DISTINCT n.medio_id) AS medios
  FROM cluster c JOIN noticia n ON n.cluster_id = c.id GROUP BY c.id
)
SELECT COUNT(*) FILTER (WHERE medios = 1) AS solo,
       COUNT(*) FILTER (WHERE medios = 2) AS con_2,
       COUNT(*) FILTER (WHERE medios > 2) AS mas_2 FROM cm;
```

Van a haber crecido respecto de lo medido el 11/09 (711 clusters, 134 solos):
el scheduler sigue ingiriendo.

---

## Anotado de paso, sin arreglar

**`topico_declarado` mira sólo el primer segmento de la URL**, y Perfil anida
todo bajo `/noticias/`, así que sus 18 clusters solos salen sin tópico. No se
tocó porque la misma función alimenta la pista que entra al prompt de síntesis:
arreglarla cambia lo que el modelo ve en cada nota de Perfil, y eso merece su
propia decisión. Es anterior al bloque F.
