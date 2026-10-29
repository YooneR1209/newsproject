// Búsqueda de noticias. Responsable: Fabricio.
// Usa GET /api/search?q=&category=&page= y GET /api/articles/{id} (Búsqueda).
// El estado vive en la URL (?q=&categoria=&pagina=&articulo=) para que el
// botón "atrás" y los enlaces compartidos funcionen.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get } from "../api.js";
import Portada from "../components/Portada.jsx";
import "./Buscar.css";

// Lista oficial (docs/eventos.md, sección 4).
const CATEGORIAS = {
  politica: "Política",
  economia: "Economía",
  tecnologia: "Tecnología",
  deportes: "Deportes",
  salud: "Salud",
  cultura: "Cultura",
  internacional: "Internacional",
  otros: "Otros",
};

const formatoFecha = new Intl.DateTimeFormat("es-EC", {
  dateStyle: "medium",
  timeStyle: "short",
});

function fecha(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : formatoFecha.format(d);
}

function mensajeError(error) {
  if (error.status === 503 || error.status === 502 || error.status === 504) {
    return "El servicio de búsqueda no está disponible. Intenta de nuevo en un momento.";
  }
  if (error.status === 404) return "No se encontró el artículo.";
  return `No se pudo completar la búsqueda (${error.message}).`;
}

function Datos({ article }) {
  return (
    <p className="buscar-datos">
      <span className="buscar-categoria">{CATEGORIAS[article.category] ?? article.category}</span>
      <span>{article.source}</span>
      <time dateTime={article.published_at}>{fecha(article.published_at)}</time>
    </p>
  );
}

function Etiquetas({ tags }) {
  if (!tags?.length) return null;
  return (
    <ul className="buscar-etiquetas">
      {tags.map((t) => (
        <li key={t}>#{t}</li>
      ))}
    </ul>
  );
}

function EnlaceFuente({ url }) {
  return (
    <a className="buscar-fuente" href={url} target="_blank" rel="noopener noreferrer">
      Leer en la fuente original ↗
    </a>
  );
}

function Resultado({ article, onAbrir }) {
  return (
    <li className="buscar-resultado">
      <button type="button" className="buscar-portada" onClick={onAbrir} aria-label={article.title}>
        <Portada article={article} />
      </button>
      <div>
        <h2 className="buscar-titulo">
          <button type="button" onClick={onAbrir}>{article.title}</button>
        </h2>
        <Datos article={article} />
        <p className="buscar-resumen">{article.summary}</p>
        <Etiquetas tags={article.tags} />
        <EnlaceFuente url={article.url} />
      </div>
    </li>
  );
}

function Detalle({ id, onVolver }) {
  const [estado, setEstado] = useState({ cargando: true, article: null, error: null });

  useEffect(() => {
    let vigente = true;
    setEstado({ cargando: true, article: null, error: null });
    get(`/articles/${encodeURIComponent(id)}`)
      .then((article) => vigente && setEstado({ cargando: false, article, error: null }))
      .catch((error) => vigente && setEstado({ cargando: false, article: null, error }));
    return () => {
      vigente = false;
    };
  }, [id]);

  const { cargando, article, error } = estado;
  return (
    <section className="buscar-detalle">
      <button type="button" className="buscar-volver" onClick={onVolver}>
        ← Volver a los resultados
      </button>
      {cargando && <p className="buscar-aviso">Cargando artículo…</p>}
      {error && <p className="buscar-aviso buscar-error">{mensajeError(error)}</p>}
      {article && (
        <article>
          <h1>{article.title}</h1>
          <Datos article={article} />
          <Portada article={article} />
          <p className="buscar-resumen">{article.summary}</p>
          <Etiquetas tags={article.tags} />
          <EnlaceFuente url={article.url} />
        </article>
      )}
    </section>
  );
}

export default function Buscar() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const categoria = CATEGORIAS[params.get("categoria")] ? params.get("categoria") : "";
  const pagina = Math.max(1, Number.parseInt(params.get("pagina"), 10) || 1);
  const articulo = params.get("articulo");

  const [texto, setTexto] = useState(q);
  const [estado, setEstado] = useState({ cargando: true, datos: null, error: null });

  // Si la URL cambia (atrás/adelante), la caja muestra la consulta actual.
  useEffect(() => setTexto(q), [q]);

  useEffect(() => {
    if (articulo) return undefined;
    let vigente = true;
    const query = new URLSearchParams({ page: String(pagina) });
    if (q) query.set("q", q);
    if (categoria) query.set("category", categoria);

    setEstado((prev) => ({ ...prev, cargando: true, error: null }));
    get(`/search?${query}`)
      .then((datos) => vigente && setEstado({ cargando: false, datos, error: null }))
      .catch((error) => vigente && setEstado({ cargando: false, datos: null, error }));
    return () => {
      vigente = false;
    };
  }, [q, categoria, pagina, articulo]);

  // Cambia la URL; los valores vacíos se quitan para que quede limpia.
  function navegar(cambios) {
    const siguiente = new URLSearchParams(params);
    for (const [clave, valor] of Object.entries(cambios)) {
      if (valor === "" || valor == null) siguiente.delete(clave);
      else siguiente.set(clave, String(valor));
    }
    setParams(siguiente);
  }

  function enviar(e) {
    e.preventDefault();
    navegar({ q: texto.trim(), pagina: null });
  }

  function irAPagina(n) {
    navegar({ pagina: n > 1 ? n : null });
    window.scrollTo({ top: 0 });
  }

  if (articulo) {
    return <Detalle id={articulo} onVolver={() => navegar({ articulo: null })} />;
  }

  const { cargando, datos, error } = estado;
  const totalPaginas = datos ? Math.max(1, Math.ceil(datos.total / datos.page_size)) : 1;

  return (
    <section>
      <h1>Buscar</h1>

      <form className="buscar-form" onSubmit={enviar} role="search">
        <input
          type="search"
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          placeholder="Buscar noticias…"
          aria-label="Texto a buscar"
          maxLength={200}
        />
        <select
          value={categoria}
          onChange={(e) => navegar({ categoria: e.target.value, pagina: null })}
          aria-label="Categoría"
        >
          <option value="">Todas las categorías</option>
          {Object.entries(CATEGORIAS).map(([valor, nombre]) => (
            <option key={valor} value={valor}>{nombre}</option>
          ))}
        </select>
        <button type="submit">Buscar</button>
      </form>

      {error && <p className="buscar-aviso buscar-error">{mensajeError(error)}</p>}

      {datos && (
        <p className="buscar-total" aria-live="polite">
          {datos.total === 0
            ? "No se encontraron noticias."
            : `${datos.total} ${datos.total === 1 ? "noticia" : "noticias"}` +
              (q ? ` para «${q}»` : " recientes") +
              (categoria ? ` en ${CATEGORIAS[categoria]}` : "")}
          {cargando && " · actualizando…"}
        </p>
      )}
      {!datos && cargando && <p className="buscar-aviso">Buscando…</p>}

      {datos?.items.length > 0 && (
        <ul className={cargando ? "buscar-lista cargando" : "buscar-lista"}>
          {datos.items.map((article) => (
            <Resultado
              key={article.article_id}
              article={article}
              onAbrir={() => navegar({ articulo: article.article_id })}
            />
          ))}
        </ul>
      )}

      {datos && totalPaginas > 1 && (
        <nav className="buscar-paginas" aria-label="Paginación">
          <button type="button" disabled={pagina <= 1 || cargando} onClick={() => irAPagina(pagina - 1)}>
            ← Anterior
          </button>
          <span>Página {pagina} de {totalPaginas}</span>
          <button
            type="button"
            disabled={pagina >= totalPaginas || cargando}
            onClick={() => irAPagina(pagina + 1)}
          >
            Siguiente →
          </button>
        </nav>
      )}
    </section>
  );
}
