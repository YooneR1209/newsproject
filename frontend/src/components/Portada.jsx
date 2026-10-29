// Imagen de portada de una noticia. Si no hay image_url o no carga,
// muestra un marcador con la categoría. Nunca se descarga ni se guarda.
import { useState } from "react";

export default function Portada({ article }) {
  const [fallo, setFallo] = useState(false);

  if (!article.image_url || fallo) {
    return <div className="portada portada-vacia">{article.category}</div>;
  }
  return (
    <img
      className="portada"
      src={article.image_url}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFallo(true)}
    />
  );
}
