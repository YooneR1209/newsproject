// Cliente REST: el navegador solo habla con /api (Nginx o el proxy de Vite).
async function request(method, ruta, body) {
  const opciones = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    opciones.headers["Content-Type"] = "application/json";
    opciones.body = JSON.stringify(body);
  }

  const res = await fetch("/api" + ruta, opciones);
  const texto = await res.text();
  let datos = null;
  try {
    datos = texto ? JSON.parse(texto) : null;
  } catch {
    datos = null;
  }

  if (!res.ok) {
    const detalle = datos?.detail ?? datos?.error ?? datos?.message;
    const mensaje =
      (typeof detalle === "string" ? detalle : detalle && JSON.stringify(detalle)) ||
      texto ||
      res.statusText;
    const error = new Error(`${res.status}: ${mensaje}`);
    error.status = res.status;
    error.data = datos;
    throw error;
  }
  return datos;
}

export const get = (ruta) => request("GET", ruta);
export const post = (ruta, body) => request("POST", ruta, body);
export const put = (ruta, body) => request("PUT", ruta, body);
export const patch = (ruta, body) => request("PATCH", ruta, body);
