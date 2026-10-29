// Usuario actual en localStorage. Puede fallar (modo privado, almacenamiento
// bloqueado): en ese caso se comporta como si no hubiera usuario.
const CLAVE = "newsproject.userId";

export function getUserId() {
  try {
    return localStorage.getItem(CLAVE);
  } catch {
    return null;
  }
}

export function setUserId(id) {
  try {
    localStorage.setItem(CLAVE, String(id));
  } catch {
    // sin almacenamiento: el usuario tendrá que registrarse de nuevo
  }
}

export function clearUser() {
  try {
    localStorage.removeItem(CLAVE);
  } catch {
    // nada que borrar
  }
}
